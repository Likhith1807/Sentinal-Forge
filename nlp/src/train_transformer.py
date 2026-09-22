"""Fine-tune the multi-task extractor on the corpus's train split, model-select on dev.

    python nlp/src/train_transformer.py --encoder roberta-base --out nlp/models/roberta-base
    python nlp/src/train_transformer.py --encoder ehsanaghaei/SecureBERT --out nlp/models/securebert
    python nlp/src/train_transformer.py --encoder roberta-base --out nlp/models/roberta-base-template-only \
        --train-tiers template   # ablation: train on the template tier only

The **test split is never read here** — only train (fit) and dev (model selection, early stopping).
Test-set numbers come only from `evaluate_corpus.py`, run once against a frozen checkpoint, so no
tuning decision is ever made by looking at test performance.

Saves the best-dev-score checkpoint plus `training_log.json` (per-epoch metrics, so a plateau or
overfit is visible, not just the final number) and `run_config.json` (unambiguous reproduction:
encoder, seed, tier filter, hyperparameters, dataset sizes).
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader
from transformers import AutoTokenizer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus_dataset import CORPUS_DIR, load_examples, CorpusExtractionDataset  # noqa: E402
from multitask_model import MultiTaskExtractor, compute_losses  # noqa: E402
from eval_common import decode_batch, score_predictions  # noqa: E402


def set_seed(seed: int) -> None:
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed); torch.cuda.manual_seed_all(seed)


def build_loader(examples, tokenizer, max_length, batch_size, shuffle):
    ds = CorpusExtractionDataset(examples, tokenizer, max_length)
    return DataLoader(ds, batch_size=batch_size, shuffle=shuffle), ds


@torch.no_grad()
def evaluate(model, loader, device) -> dict:
    model.eval()
    all_preds, all_gold, total_loss, n_batches = [], [], 0.0, 0
    for batch in loader:
        batch = {k: v.to(device) for k, v in batch.items()}
        out = model(batch["input_ids"], batch["attention_mask"])
        total_loss += compute_losses(out, batch)["total"].item()
        n_batches += 1
        all_preds += decode_batch(out)
        all_gold += decode_batch(batch, is_gold=True)
    metrics = score_predictions(all_gold, all_preds)
    metrics["loss"] = round(total_loss / max(n_batches, 1), 4)
    return metrics


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--encoder", default="roberta-base")
    ap.add_argument("--out", required=True)
    ap.add_argument("--corpus", default=str(CORPUS_DIR))
    ap.add_argument("--train-tiers", nargs="+", default=None, help="restrict TRAIN examples to these tiers (dev/test always use all tiers)")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--patience", type=int, default=8)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--weight-decay", type=float, default=0.01)
    ap.add_argument("--max-length", type=int, default=256)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args(argv)

    set_seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    out_dir = Path(args.out); out_dir.mkdir(parents=True, exist_ok=True)

    tokenizer = AutoTokenizer.from_pretrained(args.encoder)
    train_examples = load_examples(Path(args.corpus), "train")
    if args.train_tiers:
        train_examples = [e for e in train_examples if e.tier in args.train_tiers]
    dev_examples = load_examples(Path(args.corpus), "dev")
    train_loader, train_ds = build_loader(train_examples, tokenizer, args.max_length, args.batch_size, shuffle=True)
    dev_loader, dev_ds = build_loader(dev_examples, tokenizer, args.max_length, args.batch_size, shuffle=False)

    model = MultiTaskExtractor(args.encoder).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)

    run_config = {**vars(args), "device": str(device), "trainExamples": len(train_examples),
                 "devExamples": len(dev_examples),
                 "trainSpansDropped": train_ds.dropped, "devSpansDropped": dev_ds.dropped}
    (out_dir / "run_config.json").write_text(json.dumps(run_config, indent=2) + "\n", encoding="utf-8")
    print(f"train={len(train_examples)} dev={len(dev_examples)} device={device} encoder={args.encoder}"
         + (f" tiers={args.train_tiers}" if args.train_tiers else ""))

    best_score, best_epoch, bad_epochs, log = -1.0, -1, 0, []
    started = time.time()
    for epoch in range(1, args.epochs + 1):
        model.train()
        train_loss = 0.0
        for batch in train_loader:
            batch = {k: v.to(device) for k, v in batch.items()}
            optimizer.zero_grad()
            losses = compute_losses(model(batch["input_ids"], batch["attention_mask"]), batch)
            losses["total"].backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_loss += losses["total"].item()
        train_loss /= len(train_loader)

        dev_metrics = evaluate(model, dev_loader, device)
        log.append({"epoch": epoch, "trainLoss": round(train_loss, 4), **dev_metrics})
        print(f"epoch {epoch:2d}  train_loss={train_loss:.4f}  dev_loss={dev_metrics['loss']:.4f}  "
             f"behAcc={dev_metrics['behaviourAccuracy']:.3f}  fieldF1={dev_metrics['fieldF1']:.3f}  "
             f"thrExact={dev_metrics['thresholdExact']}  winExact={dev_metrics['windowExact']}  "
             f"combined={dev_metrics['combined']:.3f}")

        if dev_metrics["combined"] > best_score:
            best_score, best_epoch, bad_epochs = dev_metrics["combined"], epoch, 0
            torch.save(model.state_dict(), out_dir / "model.pt")
            (out_dir / "best_dev_metrics.json").write_text(json.dumps(dev_metrics, indent=2) + "\n", encoding="utf-8")
        else:
            bad_epochs += 1
            if bad_epochs >= args.patience:
                print(f"early stopping: no dev improvement for {args.patience} epochs (best was epoch {best_epoch})")
                break

    tokenizer.save_pretrained(out_dir)
    (out_dir / "training_log.json").write_text(json.dumps(
        {"bestEpoch": best_epoch, "bestCombinedScore": best_score, "wallSeconds": round(time.time() - started, 1),
         "epochs": log}, indent=2) + "\n", encoding="utf-8")
    print(f"best dev combined score {best_score:.3f} at epoch {best_epoch}; saved {out_dir / 'model.pt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
