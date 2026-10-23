#!/bin/sh
# Run every scenario: each concurrent one 5 times with different seeds (one wrapper call per seed, because
# the wrapper picks a fresh unique key per call; --repeat would reuse the key and fail at setup), controls
# and canaries once. Output: raw/11-<name>.txt per scenario. Set LITETRAFFIC to the CLI path.
cd "$(dirname "$0")"
CAP="--capture docker:lt-killbill-killbill-1"
for s in refund dup privacy pay adjust; do
  : > raw/11-$s.txt
  for seed in 100 101 102 103 104; do
    sh run.sh $s --seed $seed $CAP >> raw/11-$s.txt 2>&1
    echo "$s seed=$seed exit=$?"
  done
done
for s in dup-seq pay-seq adjust-seq refund-canary dup-canary pay-canary privacy-canary; do
  sh run.sh $s --seed 1 $CAP > raw/11-$s.txt 2>&1
  echo "$s exit=$?"
done
