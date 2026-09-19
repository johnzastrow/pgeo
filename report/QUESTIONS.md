# Questions Ledger

Every question the project owner asked during the work, and where the report answers it. The report's
Appendix B ("Questions asked during the project") is written from this list; review it against the
finished report before each release. Status: answered / pending (awaiting a measurement) / open
(not investigated; the report says so).

| # | Question (as asked) | Where answered | Status |
|---|---------------------|----------------|--------|
| 1 | What is the potential to reproduce these search capabilities and emit a web API straight from Postgres/PostGIS and a range of extensions? | 2.1, 3.1-3.10 (the whole pgeo study) | answered |
| 2 | Is there any value in using address data from Overture? It may or may not be better than OpenAddresses. | 2.2 (Overture evaluation) | answered |
| 3 | Evaluate Overture data as a source (Esri Parquet layers, cloud sources). | 2.2 | answered |
| 4 | Did you deploy a VM at 192.0.2.10, or locally? | 1.2, 2.1 | answered |
| 5 | Do you still need an API key from me? (OpenAddresses token) | 1.2 | answered |
| 6 | Is there any caching that may affect later results? | 2.5 (caching) | answered |
| 7 | Is all this testing just with Pelias? | 2.5, 3.4 | answered |
| 8 | How long until all the tests finish? | Appendix A (durations; REBUILD.md section 4) | answered |
| 9 | What are the latency targets? | 2.5, Table "Latency targets" | answered |
| 10 | How many users will you scale up to? | 2.5 (ramp to 512) | answered |
| 11 | Are you tuning to use the full level of parallelism Postgres allows across the cores given to it? | 2.7 (parallelism) | answered |
| 12 | Will the API of pgeo match that of Pelias, so it could be a replacement with extensions? | 3.8, 3.10 | answered |
| 13 | We need to test pgeo on the Proxmox VM for accurate tests, right? | 2.5, 3.11 | pending (VM results) |
| 14 | Which machine is doing the build? | 1.2, 2.3 | answered |
| 15 | Do the two platforms now provide feature and accuracy parity? | 3.10 | answered |
| 16 | Is there any reason to use Pelias when the pgeo results are so good? | 4 (Discussion) | answered |
| 17 | Can we use the USPS ZIP+4 data to improve addresses? | 3.9 (address API) | answered |
| 18 | Newer libpostal (Senzing libpostal-data): does it help with accuracy or speed in the US? | Appendix B, Next steps | open |
| 19 | (Requirement) When search returns a non-address, reverse geocode to the nearest high-quality street address. | 3.9 (address API) | answered |
| 20 | Minimum server resources for 3 concurrent users (Maine data)? | 3.12 | pending (floor search, temporary VMs) |
| 21 | How many users can that minimum server scale to, per platform? | 3.12 | pending |
| 22 | Which performs better, and does pgeo perform well enough at the same or lower resources? | Executive summary, 3.4, 3.10, 5 | answered |
| 23 | How do more data layers affect performance? | 3.6 | answered |
| 24 | Arguments for and against the external HTTP options (vs Postgres core/contrib only). | 2.1 | answered |
| 25 | Resources required for building and operating each platform, and which loads and data they support. | 3.5 | pending (build measurements) |
| 26 | Features each platform provides. | 3.9 | answered |
| 27 | Translate the lowest CPU finding to common shared-CPU VPS sizes (Linode nano, DigitalOcean, Vultr, Hetzner, others). | 3.5 (VPS table), 3.12 | pending (VM confirmation at 1 GB and 2 GB) |
