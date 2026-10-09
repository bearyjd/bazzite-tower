// Reproducer for "nfq_destroy_queue() not closed: -1" (rollout gate 2).
// Hypothesis: a stale NLMSG_ERROR reply to an earlier verdict (for an entry the
// kernel flushed when another base chain was unregistered) is still queued on
// the netlink socket when nfq_destroy_queue() runs; libnfnetlink's nfnl_catch
// reads it first (every reply has seq 0: libnetfilter_queue disables sequence
// tracking), nfnl_step sees the error and nfq_destroy_queue returns -1, ENOENT.
// Usage: repro [drain]   (run inside a throwaway netns with CAP_NET_ADMIN)
#include <arpa/inet.h>
#include <errno.h>
#include <libnetfilter_queue/libnetfilter_queue.h>
#include <linux/netfilter.h>
#include <linux/netlink.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

static uint32_t last_id;
static int packets;

static int cb(struct nfq_q_handle *qh, struct nfgenmsg *m, struct nfq_data *nfa, void *d) {
    (void)qh; (void)m; (void)d;
    struct nfqnl_msg_packet_hdr *ph = nfq_get_msg_packet_hdr(nfa);
    if (ph) { last_id = ntohl(ph->packet_id); packets++; }
    return 1; /* no verdict here: the test sends it later */
}

static void show_queues(const char *when) {
    char line[256]; int any = 0;
    FILE *f = fopen("/proc/net/netfilter/nfnetlink_queue", "r");
    printf("kernel queues %s:", when);
    while (f && fgets(line, sizeof(line), f)) { line[strcspn(line, "\n")] = 0; printf(" [%s]", line); any = 1; }
    if (!any) printf(" none");
    printf("\n");
    if (f) fclose(f);
}

static void send_udp(void) {
    int s = socket(AF_INET, SOCK_DGRAM, 0);
    struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons(9999)};
    inet_pton(AF_INET, "127.0.0.1", &a.sin_addr);
    if (sendto(s, "x", 1, 0, (struct sockaddr *)&a, sizeof(a)) != 1) perror("sendto");
    close(s);
}

static void peek_pending(int fd) {
    char buf[8192] __attribute__((aligned));
    int n = recv(fd, buf, sizeof(buf), MSG_PEEK | MSG_DONTWAIT);
    if (n <= 0) { printf("pending: none\n"); return; }
    struct nlmsghdr *nlh = (struct nlmsghdr *)buf;
    if (nlh->nlmsg_type == NLMSG_ERROR) {
        struct nlmsgerr *e = NLMSG_DATA(nlh);
        printf("pending: NLMSG_ERROR seq=%u error=%d (%s)\n", nlh->nlmsg_seq, e->error, strerror(-e->error));
    } else {
        printf("pending: type=0x%x seq=%u\n", nlh->nlmsg_type, nlh->nlmsg_seq);
    }
}

int main(int argc, char **argv) {
    int drain = argc > 1 && strcmp(argv[1], "drain") == 0;
    char buf[8192] __attribute__((aligned));
    struct nfq_handle *h = nfq_open();
    if (!h) { perror("nfq_open"); return 2; }
    struct nfq_q_handle *qh = nfq_create_queue(h, 7, cb, NULL);
    if (!qh) { perror("nfq_create_queue"); return 2; }
    if (nfq_set_mode(qh, NFQNL_COPY_PACKET, 0xffff) < 0) { perror("nfq_set_mode"); return 2; }
    int fd = nfq_fd(h), one = 1;
    setsockopt(fd, SOL_NETLINK, NETLINK_NO_ENOBUFS, &one, sizeof(one));

    send_udp();
    int n = recv(fd, buf, sizeof(buf), 0);
    nfq_handle_packet(h, buf, n);
    if (packets != 1) { fprintf(stderr, "expected one queued packet, got %d\n", packets); return 2; }
    printf("queued packet id=%u\n", last_id);

    /* Unregistering any hook in this netns flushes every queued entry. */
    if (system("nft add chain inet t churn '{ type filter hook input priority 0 ; }' && nft delete chain inet t churn") != 0) return 2;
    int v = nfq_set_verdict(qh, last_id, NF_ACCEPT, 0, NULL);
    printf("late verdict send=%d\n", v);
    peek_pending(fd);

    if (drain) {
        int drained = 0;
        while ((n = recv(fd, buf, sizeof(buf), MSG_DONTWAIT)) > 0) { nfq_handle_packet(h, buf, n); drained++; }
        printf("drained %d message(s) before destroy\n", drained);
    }
    show_queues("before destroy");
    errno = 0;
    int r = nfq_destroy_queue(qh);
    int saved = errno;
    printf("nfq_destroy_queue=%d errno=%d (%s)\n", r, saved, strerror(saved));
    show_queues("after destroy");
    nfq_close(h);
    show_queues("after nfq_close");
    return 0;
}
