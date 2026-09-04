# If this had to serve 20M MAU

Arete is built for one person. This is what would have to change if it were not,
and, as importantly, what would not.

The numbers below assume 20M monthly and 5M daily active users, 15% of whom
capture something on a given day, at 2.5 clips each. Median clip 30 seconds at
12 Mbps, so about 45 MB. Roughly 30% of clips are ever shared, and a shared clip
is watched about 8 times.

| | |
|---|---|
| Clips created | 1.9M/day, 22/s average, ~110/s at a 5x peak |
| Ingest | 84 TB/day, 7.8 Gb/s average |
| Stored, if nothing is ever deleted | 31 PB/year |
| Views | 4.5M/day |
| Egress | 40 TB/day, 3.8 Gb/s average |
| Rows in `clips` | 684M/year |

Those numbers decide everything else. They are also what I would want measured
before building any of this, because two of the assumptions carry most of the
cost and both are guesses: **what fraction of captured clips are ever shared**,
and **how many views a shared clip gets**. If sharing is 5% rather than 30%, the
storage bill is a sixth of the above and lazy transcoding stops being optional.
If a shared clip averages 80 views rather than 8, delivery dominates and the
rest is a rounding error.

## What does not change

**Capture stays on the player's GPU.** This is the decision that makes the rest
affordable. Encoding 1.9M clips a day server-side would be the entire business;
done at the edge it costs nothing, because the expensive work is spread across
five million machines that have already paid for the silicon. The ring buffer is
the same shape at 1 user or 5M: a fixed window on local disk that overwrites
itself.

**Video bytes never pass through the API tier.** The client asks for an upload
target, PUTs to storage directly, then confirms. At 7.8 Gb/s of ingest, an API
in that path would have to scale with bytes rather than requests. It stays a
metadata service that never touches a frame.

**The cut is a remux.** Every segment opens on an IDR frame, so trimming is a
byte copy. Any design where the server re-encodes on ingest has a GPU fleet
attached to it. This one does not need one.

**Keyset pagination.** `list_clips` orders by `(captured_at desc, id desc)` and
seeks with a cursor. At 684M rows a year, `OFFSET` on a deep page reads and
discards everything before it. It was free to do correctly at a thousand rows
and is the only workable option at a billion.

**Capture and publish stay separate.** Holding a clip until someone asks for a
link is a product decision at one user. At scale it is also the cheapest line in
the budget: if 70% of clips are never shared, keeping them local means never
paying to store, transcode or serve them. Uploading on capture would raise
ingest from 84 TB/day to 280 TB/day to serve the same 4.5M views.

## What breaks, in the order it breaks

**1. SQLite, immediately.** One writer, one file, one machine. It goes to
Postgres for write concurrency, then `clips` is partitioned by month, because
684M rows a year makes index maintenance and vacuum the dominant cost, and
because expiry becomes a partition drop rather than a mass delete. Past roughly
a billion rows I would shard by `owner_id`: a library query is always scoped to
one owner, so there is no cross-shard read on the hot path.

**2. The single process.** `desktop.py` runs the API, the capture and the window
together, which is right for something people install and wrong for a service.
The API becomes a stateless tier behind a load balancer, scaled on request rate,
with capture staying entirely on the client where it already is.

**3. Local disk.** 31 PB/year does not fit anywhere, so this is where the things
deliberately left out stop being optional:

- **Retention.** Clips nobody has opened in 90 days move to cold storage;
  unshared clips expire outright. Without a policy, storage grows without bound
  against revenue that does not.
- **Storage tiering.** Hot for a week, since most views land in the first 48
  hours, then infrequent access, then archive. The steeply front-loaded access
  pattern is what makes tiering worth its complexity.
- **Dedup.** `content_hash` is already computed on ingest and used only to catch
  a re-upload. At scale it earns its keep for a reason specific to this product:
  the same highlight gets shared repeatedly, and collapsing an identical
  re-upload is free.

**4. Delivery.** 40 TB/day of egress is where the bill is decided. It goes
behind a CDN, and the origin is chosen for egress pricing rather than storage
pricing: at 2 cents per GB that is ~$300k/year, and on a provider that does not
charge for egress it is zero. The largest cost lever in the system is a
procurement decision, not an engineering one.

**5. One rendition on ingest.** Every clip is transcoded once when it arrives.
Since ~70% are never watched, that is 70% of the transcode bill spent on
nothing. It becomes lazy: generate on first playback, cache, keep the source.
Eager transcoding only makes sense while it is nearly free, which it is at one
user and is not at 1.9M clips a day.

**6. The tunnel.** A quick tunnel takes a new hostname every launch, which is
why links here do not outlive a session. Real DNS on a domain, anycast, and
share URLs that resolve for as long as the clip exists.

**7. API keys.** One per machine, hashed, with no revocation short of editing a
row. 20M accounts needs real identity: OIDC, short-lived access tokens with
refresh, immediate revocation, and rate limiting per account rather than per
process. The 404-not-403 rule on someone else's clip already generalises and
would stay.

## What gets added that has no equivalent here

**A client fleet is a distributed system.** Five million copies of a recorder on
hardware you do not control is the hardest part of this, and none of it is in
this repository:

- Staged rollout by cohort, because a build that wedges the encoder is
  discovered by users pressing a key and getting nothing.
- A kill switch for capture, so a driver-specific crash can be stopped without
  waiting for an update to land.
- Crash and health telemetry, with consent. The watchdog here restarts a dead or
  wedged encoder and writes to a local log. At scale the signal worth having is
  the rate of those restarts by GPU model and driver version.
- Resumable upload. Retry is per-clip through a journal so nothing is lost, but
  a 90%-complete upload of a 45 MB file starts over. At 1.9M uploads a day on
  residential connections that is real waste, and the API is already shaped
  around upload targets, so multipart drops in.

**Abuse and moderation.** A link anyone can open, attached to arbitrary user
video, needs takedown, hashing against known material, reporting, and rate
limits on creation and sharing. Not a feature so much as the cost of admission
for hosting other people's video publicly.

**An event pipeline.** View counts here are a column incremented in place: write
amplification the moment a clip is popular, and a lost update the moment there
are two API instances. Views become events on a queue, aggregated
asynchronously, with the count on the clip row a materialised value rather than
the source of truth.

## The summary

Three decisions here would survive a hundredfold increase: capture on the
client's GPU, bytes that never touch the API tier, and publishing as a step
separate from capture. None were chosen for scale. They were chosen because they
make a single-user application fast and cheap, and they happen to be what scale
would force anyway.

Everything else, SQLite, a file on disk, one process, a quick tunnel, a key per
machine, is right for one user and wrong for a million, and was picked knowing
that.
