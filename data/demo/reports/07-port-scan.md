# Internal reconnaissance from a jump host

Network sensors saw JUMP-03 probing many ports on neighbouring machines. It touched 725 distinct destination ports in well under a minute.

Ask: alert when one host connects to more than 200 distinct destination ports within 5 minutes. Evaluating the rule requires `destination_port` and `dst_ip` from the network flow log.
