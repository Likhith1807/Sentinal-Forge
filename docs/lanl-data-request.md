# LANL Data Access Request (draft — not sent)

LANL's [cyber1 page](https://csr.lanl.gov/data/cyber1/) states that the data
is CC0 and that download requires emailing **cyberdata@lanl.gov** with a
brief description of intended use. The page does not state a response time.

Fill the `[bracketed]` items, send it from your own institutional or personal
account, and keep the reply — it is the provenance record for the dataset.

---

**To:** cyberdata@lanl.gov
**Subject:** Data access request — Comprehensive, Multi-Source Cyber-Security Events (cyber1)

Hello,

I am [your full name], a [degree / year] student at [institution, department].
I would like to request access to the "Comprehensive, Multi-Source
Cyber-Security Events" data set (cyber1), specifically `auth.txt.gz` and
`redteam.txt.gz`.

**Intended use.** An academic project, [SENTINEL Forge], that compiles
threat-report descriptions into typed detection rules and evaluates them on
authentication telemetry. I will use the authentication events as realistic
background traffic (account, source computer and success/failure structure),
inject separately labelled synthetic attack scenarios into it, and measure
detection correctness and throughput. `redteam.txt` would be used only as a
secondary recall check.

**Handling.** The data will be stored locally and will not be redistributed;
only derived aggregate results will be published. Fields the project's
schema does not cover (for example authentication type and logon type) are
dropped, and fields LANL does not provide (MFA status, authentication method,
account policy) are generated synthetically and labelled as such in the
project documentation. I will cite the data set as requested:

> Kent, A.D. (2015). Comprehensive, Multi-Source Cyber-Security Events.
> Los Alamos National Laboratory. DOI: 10.17021/1179829.

Please let me know if you need anything further, or if there is a preferred
way to download the files.

Thank you,
[Your full name]
[Institution, department]
[Supervisor / course coordinator, if applicable]
[Your email]

---

## After the reply

1. Download into `data/external/lanl/` (git-ignored; never commit raw LANL data).
2. Run the mapper on a slice first and read the value profile before the full run:

   ```
   python -m scripts.datagen.lanl_mapper --auth data/external/lanl/auth.txt.gz \
       --out data/generated/lanl_trial --max-rows 5000000
   ```

3. Compare `mapper_manifest.json`'s `valueProfile` against the assumptions in
   [`data-sources.md`](data-sources.md#lanl-format-assumptions-to-verify-against-the-real-file)
   (orientation `LogOn`, status `Success`/`Fail`, `$` machine accounts). The
   mapper raises on unrecognised values rather than guessing.
4. If satisfied, run the full mapping, then inject incidents with
   `--background-dir` (see `scripts/datagen/README.md`).
