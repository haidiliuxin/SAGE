# -*- coding: utf-8 -*-
import json
import os
import sys
import time


def arg_value(flag):
    try:
        index = sys.argv.index(flag)
    except ValueError:
        return None
    try:
        return sys.argv[index + 1]
    except IndexError:
        return None


outfile = arg_value("--outfile")
attack_mode = arg_value("--attack-mode") or "0"
slow = float(os.environ.get("FAKE_HASHCAT_SLOW", "0"))
no_recover = os.environ.get("FAKE_HASHCAT_NO_RECOVER", "") == "1"
hex_plain = os.environ.get("FAKE_HASHCAT_HEX_PLAIN", "") == "1"

if "--restore" in sys.argv:
    print(json.dumps({"progress": [0, 0], "percent": 100}))
    sys.exit(1)

args = sys.argv[1:]
target_path = args[-2] if attack_mode in {"0", "3"} else args[-3]
wordlist = None
masks = []
if attack_mode == "0":
    wordlist = args[-1]
elif attack_mode == "3":
    masks = [args[-1]]
elif attack_mode == "6":
    wordlist = args[-2]
    masks = [args[-1]]
elif attack_mode == "7":
    masks = [args[-2]]
    wordlist = args[-1]

if wordlist:
    with open(wordlist, encoding="utf-8") as fh:
        candidates = [line.rstrip("\r\n") for line in fh]
else:
    candidates = []
if masks and os.path.isfile(masks[0]):
    with open(masks[0], encoding="utf-8") as fh:
        candidates = [line.rstrip("\r\n") for line in fh]
elif masks:
    candidates = masks
with open(target_path, encoding="utf-8") as fh:
    targets = [line.rstrip("\r\n") for line in fh if line.strip()]

total = len(candidates)
if slow:
    time.sleep(slow)

log_path = os.environ.get("FAKE_HASHCAT_LOG", "")
if log_path:
    with open(log_path, "a", encoding="utf-8") as lf:
        lf.write(json.dumps({
            "argv": sys.argv[1:],
            "hash_type": arg_value("--hash-type"),
            "attack_mode": attack_mode,
            "targets": targets,
            "candidate_count": total,
            "masks": masks,
            "rules": [args[index + 1] for index, arg in enumerate(args) if arg == "-r" and index + 1 < len(args)],
        }, ensure_ascii=False) + "\n")

print(json.dumps({"progress": [total, 0], "percent": 100}))
sys.stdout.flush()

if not no_recover and outfile and candidates and targets:
    plain = candidates[0]
    if hex_plain:
        plain = "$HEX[" + plain.encode("utf-8").hex().upper() + "]"
    with open(outfile, "w", encoding="utf-8") as fh:
        fh.write(targets[0] + "\t" + plain + "\n")
sys.exit(0)
