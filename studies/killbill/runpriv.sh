#!/bin/sh
# Re-run the privacy scenario 5 seeds + its canary after fixing my own search rule. Output: raw/11-privacy*.txt
cd "$(dirname "$0")"
CAP="--capture docker:lt-killbill-killbill-1"
: > raw/11-privacy.txt
for seed in 100 101 102 103 104; do
  sh run.sh privacy --seed $seed $CAP >> raw/11-privacy.txt 2>&1
  echo "privacy seed=$seed exit=$?"
done
sh run.sh privacy-canary --seed 1 $CAP > raw/11-privacy-canary.txt 2>&1
echo "privacy-canary exit=$?"
