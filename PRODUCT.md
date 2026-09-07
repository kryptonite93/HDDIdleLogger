# HDD Idle Profiler

<!-- impeccable:product-schema 1 -->

## Platform
web

## Stack
The supplied handoff specifies Python 3.12, FastAPI, SQLite, Jinja2 and vanilla JavaScript/CSS. Charts use HTML/CSS, an explicitly open implementation choice in the handoff.

## Users
An Unraid owner with over 20 spinning disks, profiling media-server workloads for days or weeks before manually choosing spin-down delays.

## Product Purpose
Measure observed idle gaps from kernel counters and compare modeled spin cycles and down time for each disk and the array.

## Operating Context
A Docker container on a trusted LAN/VPN. The owner will run the Unraid acceptance steps. GitHub namespace: kryptonite93. Working repository name follows the local folder: HDDIdleLogger; remote existence is unverified. License undecided; personal use requested.

Owner-confirmed deployment target: Unraid 7.3.1, pool named `cache`. Use `/mnt/cache/appdata/hdd-idle-profiler` for persistent appdata. Host acceptance has not yet been performed.

## Capabilities and Constraints
Read counters and optional kernel metadata only. No disk power commands, file scanning, SMART, Unraid settings changes, external services or telemetry. Persist history outside the monitored array when possible. Exclude downtime and unknown initial idle durations from recommendations. Both per-disk and array analysis, with confidence gated on 72 valid hours.

## Evidence on Hand
Unraid-HDD-Idle-Profiler-Project-Handoff.md is the product specification. No actual drive observations are present. Preview data must be labeled synthetic.

## Product Principles
Observed I/O is not actual standby. Show calculations behind advice. Preserve gaps in knowledge. Keep collection safe and lightweight.
