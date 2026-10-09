# `nfq_destroy_queue() not closed: -1` at daemon stop

Investigated 2026-10-07. The warning was the last open part of Snitchwatch
rollout gate 2 (see [snitchwatch-system-bridge.md](../snitchwatch-system-bridge.md)):
on the r3 VM it appeared three times, each in a daemon stop just before a
reboot, while timed stops were clean.

## Root cause

1. Any nftables base chain being unregistered in the netns (other services
   remove theirs at shutdown) makes the kernel's `nfqnl_nf_hook_drop` flush
   every queued entry of every NFQUEUE instance.
2. The daemon's verdict for a flushed entry gets an `NLMSG_ERROR` reply
   (`ENOENT`, sequence 0) on the queue socket.
3. Teardown stops the reader through its wake pipe, so that reply can stay
   unread.
4. `nfq_destroy_queue()` sends `NFQNL_CFG_CMD_UNBIND` and reads replies with
   libnfnetlink's `nfnl_catch()`, which stops at the first error it sees
   (`nfnl_step`, libnfnetlink 1.0.1). It takes the stale verdict error for
   its own reply and returns -1 with `errno = ENOENT`.

The unbind itself succeeds: the kernel queue is gone straight after the
failed call. Effect: a misleading warning and a leaked queue handle at exit,
not a queue left bound. The earlier instrumented diagnostics (a stale
negative ACK before the UNBIND ACK) describe the same sequence.

## Reproduction

[`repro.c`](repro.c) uses libnetfilter_queue directly in a rootless container
network namespace (needs `CAP_NET_ADMIN` there, `nft`, and the host's
`nfnetlink_queue` module loaded): queue one UDP packet, add and delete a base
chain to flush it, send the late verdict, then destroy, optionally draining
the socket first. Raw output: [`repro-results.txt`](repro-results.txt).

```sh
nft add table inet t
nft add chain inet t out '{ type filter hook output priority 0 ; }'
nft add rule inet t out udp dport 9999 queue num 7
gcc -o repro repro.c -lnetfilter_queue -lnfnetlink
./repro          # nfq_destroy_queue=-1 errno=2, queue already unbound
./repro drain    # nfq_destroy_queue=0
```

In the recorded runs the unpatched order failed 4 of 4 times and draining
first succeeded 4 of 4: once with `repro.c`'s own drain loop, three times
with that loop replaced by the patch's `DrainPending()` (built against the
patched `queue.h`).

## Fix

The downstream patch adds `DrainPending()` to `queue.h` and calls it in
`Queue.destroy()` before `nfq_destroy_queue()`. Destroy only runs after the
reader and its callbacks have joined, so nothing else reads the socket; packet
notifications drained there are retired exactly as `nfnl_catch` would have
retired them. The drain is non-blocking and bounded. Both teardown warnings
now include `errno`. Unit test: `netfilter/testdata/queue_drain.c`.
