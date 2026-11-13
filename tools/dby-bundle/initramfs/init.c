// SPDX-License-Identifier: GPL-2.0-only
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <dirent.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/utsname.h>
#include <sys/reboot.h>
#include <sys/ioctl.h>
#include <linux/fs.h>
#include <sys/klog.h>
#include <ctype.h>
#include <sys/syscall.h>
#include <linux/mount.h>
#include <linux/loop.h>
#include <stdint.h>
#include "device-pack.h"
#include "safe-mode.h"
static void show(const char *path) {
    char b[512]; FILE *f=fopen(path,"r");
    printf("%s:\n",path); if(!f){perror(path);return;}
    while(fgets(b,sizeof(b),f)) fputs(b,stdout);
    fclose(f);
}
static int readonly(const char *path) {
    int one=1, fd=open(path,O_RDONLY|O_CLOEXEC), ret;
    if(fd<0) return -1;
    ret=ioctl(fd,BLKROSET,&one); close(fd); return ret;
}
/* Kernel log filtering: pure in-memory selection and truncation. */
static int log_line_matches(const char *line, size_t len)
{
    static const char *const keys[] = {
        "sync_state", "simple-framebuffer", "simpledrm", "fbcon", "arm-smmu",
        "sdhci", "mmc", "rpmhpd", "interconnect", "error", "fail", "warn"
    };
    size_t k, i, j, n;
    for (k = 0; k < sizeof(keys) / sizeof(keys[0]); k++) {
        n = strlen(keys[k]);
        for (i = 0; i + n <= len; i++) {
            for (j = 0; j < n; j++)
                if (tolower((unsigned char)line[i + j]) != keys[k][j]) break;
            if (j == n) return 1;
        }
    }
    return 0;
}
static size_t filter_kernel_log(const char *data, size_t bytes,
                               void (*emit)(const char *))
{
    char lines[40][111];
    size_t offset = 0, matches = 0, len, shown, first, i, n;
    const char *end;
    while (offset < bytes) {
        end = memchr(data + offset, '\n', bytes - offset);
        len = end ? (size_t)(end - (data + offset)) : bytes - offset;
        n = len;
        if (len && data[offset + len - 1] == '\r') len--;
        if (log_line_matches(data + offset, len)) {
            i = matches++ % 40;
            shown = len < 110 ? len : 110;
            memcpy(lines[i], data + offset, shown);
            lines[i][shown] = 0;
        }
        offset += n + (end != NULL);
    }
    shown = matches < 40 ? matches : 40;
    first = matches > 40 ? matches % 40 : 0;
    for (i = 0; i < shown; i++) emit(lines[(first + i) % 40]);
    return matches;
}
enum { SYSLOG_ACTION_READ_ALL = 3, SYSLOG_ACTION_SIZE_BUFFER = 10 };
static void emit_kernel_line(const char *line) { puts(line); }
static void show_kernel_log(void)
{
    int size = klogctl(SYSLOG_ACTION_SIZE_BUFFER, NULL, 0), got;
    char *buffer;
    size_t matches;
    if (size < 0) { perror("klogctl buffer size"); return; }
    if (!size) { puts("Kernel log buffer is empty"); return; }
    buffer = malloc((size_t)size + 1);
    if (!buffer) { perror("kernel log allocation"); return; }
    got = klogctl(SYSLOG_ACTION_READ_ALL, buffer, size);
    if (got < 0) perror("klogctl READ_ALL");
    else {
        puts("KEY KERNEL LOG: last 40 matching lines, max 110 bytes per line");
        matches = filter_kernel_log(buffer, (size_t)got, emit_kernel_line);
        printf("Kernel log matches=%zu shown=%zu\n", matches, matches < 40 ? matches : 40);
    }
    free(buffer);
}
/* Root validation and injectable control flow for host tests. */
static const char *superblock_reason(const unsigned char *s, size_t size)
{
    static const unsigned char label[16] = "ubuntu-root";
    if (size != 1024) return "short ext4 superblock";
    if (s[0x38] != 0x53 || s[0x39] != 0xef) return "ext4 magic mismatch";
    if (memcmp(s + 0x78, label, sizeof(label))) return "ext4 label is not ubuntu-root";
    return NULL;
}
struct root_ops {
    int (*wait_partition)(void *);
    int (*protect_p1)(void *);
    int (*read_super)(void *, unsigned char *);
    int (*mount_root)(void *);
    int (*check_init)(void *);
    int (*prepare_device)(void *);
    int (*switch_exec)(void *);
    int (*cleanup)(void *);
};
static int root_attempt(const struct root_ops *o, void *c, const char **why)
{
    unsigned char sb[1024];
    int rc;
#define ROOT_STEP(member, text) do { *why = text; rc = o->member(c); if (rc) goto fail; } while (0)
    ROOT_STEP(wait_partition, "p2 not available within 20 seconds");
    ROOT_STEP(protect_p1, "p1 BLKROSET failed");
    *why = "reading p2 ext4 superblock failed";
    rc = o->read_super(c, sb); if (rc) goto fail;
    *why = superblock_reason(sb, sizeof(sb));
    if (*why) { rc = -EINVAL; goto fail; }
    ROOT_STEP(mount_root, "ext4 mount failed");
    ROOT_STEP(check_init, "missing or unusable /sbin/init");
    puts("SWITCHING TO UBUNTU");
    (void)o->prepare_device(c); /* Optional device pack never blocks the p2 boot. */
    ROOT_STEP(switch_exec, "switch_root or execv /sbin/init failed");
#undef ROOT_STEP
    return 0; /* Host model only: a real successful exec never returns. */
fail:
    { int cleanup_rc = o->cleanup(c);
      if (cleanup_rc) printf("ROOT CLEANUP FAILED errno=%d (%s)\n", -cleanup_rc, strerror(-cleanup_rc)); }
    return rc;
}

/* Optional hash-checked, read-only device pack. No runtime arguments. */
#ifndef DEVICE_TEST_ROOT
#define DEVICE_TEST_ROOT ""
#endif
#define DNR DEVICE_TEST_ROOT "/newroot"
#define DFW DEVICE_TEST_ROOT "/sys/module/firmware_class/parameters/path"
#define DDEV DEVICE_TEST_ROOT "/dev"
#define DRUN DNR "/run"
#define DMOD DNR "/usr/lib/modules/7.2.10"
struct device_context {
    int run_mounted, p1_mounted, image_mounted, module_mounted, module_created;
    int loop_fd, fw_changed;
    unsigned char copied[DEVICE_CONFIG_COUNT];
    char previous_fw[257];
};
/* SHA-256 (FIPS 180-4 compression); file is opened once and kept for LOOP_CONFIGURE. */
struct sha256 { uint32_t h[8]; uint64_t bytes; unsigned used; unsigned char buf[64]; };
static uint32_t ror(uint32_t x, unsigned n) { return (x >> n) | (x << (32-n)); }
static void sha_block(struct sha256 *s, const unsigned char *p)
{
    static const uint32_t k[64] = {
        0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
        0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
        0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
        0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
        0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
        0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
        0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
        0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
    uint32_t w[64],a,b,c,d,e,f,g,h,t1,t2; unsigned i;
    for(i=0;i<16;i++) w[i]=((uint32_t)p[i*4]<<24)|((uint32_t)p[i*4+1]<<16)|((uint32_t)p[i*4+2]<<8)|p[i*4+3];
    for(i=16;i<64;i++) w[i]=(ror(w[i-2],17)^ror(w[i-2],19)^(w[i-2]>>10))+w[i-7]+(ror(w[i-15],7)^ror(w[i-15],18)^(w[i-15]>>3))+w[i-16];
    a=s->h[0]; b=s->h[1]; c=s->h[2]; d=s->h[3]; e=s->h[4]; f=s->h[5]; g=s->h[6]; h=s->h[7];
    for(i=0;i<64;i++) {
        t1=h+(ror(e,6)^ror(e,11)^ror(e,25))+((e&f)^(~e&g))+k[i]+w[i];
        t2=(ror(a,2)^ror(a,13)^ror(a,22))+((a&b)^(a&c)^(b&c));
        h=g; g=f; f=e; e=d+t1; d=c; c=b; b=a; a=t1+t2;
    }
    s->h[0]+=a; s->h[1]+=b; s->h[2]+=c; s->h[3]+=d; s->h[4]+=e; s->h[5]+=f; s->h[6]+=g; s->h[7]+=h;
}
static void sha_feed(struct sha256 *s, const unsigned char *p, size_t n)
{
    size_t k;
    s->bytes+=n;
    while(n) {
        k=64-s->used; if(k>n) k=n;
        memcpy(s->buf+s->used,p,k); s->used+=(unsigned)k; p+=k; n-=k;
        if(s->used==64) { sha_block(s,s->buf); s->used=0; }
    }
}
static int hash_fd(int fd, char hex[65])
{
    struct sha256 s={.h={0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19}};
    unsigned char buf[32768],tail[128]={0x80}; uint64_t bits; size_t pad; unsigned i;
    ssize_t n;
    if(lseek(fd,0,SEEK_SET)<0) return -1;
    while((n=read(fd,buf,sizeof(buf)))!=0) {
        if(n<0) { if(errno==EINTR) continue; return -1; }
        sha_feed(&s,buf,(size_t)n);
    }
    bits=s.bytes*8; pad=s.used<56?56-s.used:120-s.used;
    for(i=0;i<8;i++) tail[pad+i]=(unsigned char)(bits>>(56-i*8));
    sha_feed(&s,tail,pad+8);
    for(i=0;i<8;i++) snprintf(hex+i*8,9,"%08x",s.h[i]);
    return lseek(fd,0,SEEK_SET)<0?-1:0;
}
static int write_all(int fd, const void *ptr, size_t size)
{
    const char *p=ptr;
    while(size) {
        ssize_t n=write(fd,p,size);
        if(n<0 && errno==EINTR) continue;
        if(n<=0) return -1;
        p+=n; size-=(size_t)n;
    }
    return 0;
}
static int directory(const char *p)
{
    struct stat st;
    return lstat(p,&st)==0 && S_ISDIR(st.st_mode);
}
static int run_directory(const char *p)
{
    if(mkdir(p,0755) && errno!=EEXIST) return -1;
    return directory(p)?0:-1;
}
static int device_status(const char *text)
{
    int fd=open(DRUN "/dby-device.status",O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW|O_CLOEXEC,0644),rc;
    if(fd<0) return -1;
    rc=write_all(fd,text,strlen(text));
    if(!rc) rc=write_all(fd,"\n",1);
    if(close(fd) && !rc) rc=-1;
    return rc;
}
static int firmware_path(const char *value, int empty)
{
    int fd=open(DFW,O_WRONLY|O_CLOEXEC),rc;
    if(fd<0) return -1;
    /* Empty sysfs char-array parameters need a NUL byte, never a newline. */
    rc=write_all(fd,value,empty?1:strlen(value));
    if(close(fd) && !rc) rc=-1;
    return rc;
}
static int detach(const char *p)
{
    if(!umount(p) || !umount2(p,MNT_DETACH)) return 0;
    return -1;
}
static int device_release(struct device_context *c, int keep_run)
{
    int rc=0; unsigned i;
    char path[1024];
    for(i=0;i<DEVICE_CONFIG_COUNT;i++) if(c->copied[i]) {
        snprintf(path,sizeof(path),DRUN "/%s",device_configs[i]);
        if(unlink(path) && errno!=ENOENT) rc=-1;
        else c->copied[i]=0;
    }
    if(c->fw_changed) {
        if(firmware_path(c->previous_fw,!c->previous_fw[0])) rc=-1;
        else c->fw_changed=0;
    }
    if(c->module_mounted) { if(detach(DMOD)) rc=-1; else c->module_mounted=0; }
    if(c->image_mounted) { if(detach(DRUN "/dby-device")) rc=-1; else c->image_mounted=0; }
    if(c->loop_fd>=0) { close(c->loop_fd); c->loop_fd=-1; }
    if(c->p1_mounted) { if(detach(DRUN "/dby-p1")) rc=-1; else c->p1_mounted=0; }
    if(c->module_created && !c->module_mounted) {
        if(rmdir(DMOD) && errno!=ENOENT) rc=-1; else c->module_created=0;
    }
    /* Keep the normal /run tmpfs for the failure status and systemd handoff. */
    if(!keep_run && c->run_mounted) { if(detach(DRUN)) rc=-1; else c->run_mounted=0; }
    return rc;
}
static int config_copy(struct device_context *c, unsigned index)
{
    char from[1024],to[1024],target[1024],parent[1024],buf[16384];
    struct stat st; int in=-1,out=-1,rc=-1; ssize_t n; size_t i;
    snprintf(from,sizeof(from),DRUN "/dby-device/etc/%s",device_configs[index]);
    snprintf(to,sizeof(to),DRUN "/%s",device_configs[index]);
    snprintf(parent,sizeof(parent),"%s",to);
    for(i=strlen(DRUN)+1;parent[i];i++) if(parent[i]=='/') {
        parent[i]=0;
        if(run_directory(parent)) return -1;
        parent[i]='/';
    }
    if(lstat(from,&st)) return -1;
    if(S_ISLNK(st.st_mode)) {
        n=readlink(from,target,sizeof(target)-1);
        if(n<0 || n>=(ssize_t)sizeof(target)-1) return -1;
        target[n]=0;
        if(symlink(target,to)) return -1;
        c->copied[index]=1;
        return 0;
    }
    if(!S_ISREG(st.st_mode)) return -1;
    in=open(from,O_RDONLY|O_NOFOLLOW|O_CLOEXEC);
    if(in<0) return -1;
    out=open(to,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,st.st_mode&0777);
    if(out<0) goto end;
    c->copied[index]=1;
    while((n=read(in,buf,sizeof(buf)))!=0) {
        if(n<0) { if(errno==EINTR) continue; goto end; }
        if(write_all(out,buf,(size_t)n)) goto end;
    }
    if(fchmod(out,st.st_mode&0777)) goto end;
    rc=0;
end:
    if(out>=0 && close(out)) rc=-1;
    close(in);
    return rc;
}
static const char *device_setup(struct device_context *c)
{
    struct stat st; struct loop_config loop={0};
    int image=-1,ctl=-1,idx,oldfd; ssize_t n; unsigned i;
    char hex[65],loop_path[128]; const char *why="FAIL_RUN";
    /* /run must already be a directory; no p2 mkdir except the module version. */
    if(!directory(DRUN) || mount("tmpfs",DRUN,"tmpfs",MS_NOSUID|MS_NODEV,"mode=0755")) goto fail;
    c->run_mounted=1;
    why="FAIL_P1";
    if(run_directory(DRUN "/dby-p1") || mount(DDEV "/mmcblk0p1",DRUN "/dby-p1","vfat",MS_RDONLY|MS_NOSUID|MS_NODEV|MS_NOEXEC,NULL)) goto fail;
    c->p1_mounted=1;
    why="FAIL_IMAGE_MISSING";
    image=open(DRUN "/dby-p1/" DEVICE_FILE,O_RDONLY|O_NOFOLLOW|O_CLOEXEC);
    if(image<0 || fstat(image,&st) || !S_ISREG(st.st_mode) || st.st_size<=0) goto fail;
    why="FAIL_HASH";
    if(hash_fd(image,hex) || strcmp(hex,DEVICE_SHA256)) goto fail;
    why="FAIL_LOOP";
    ctl=open(DDEV "/loop-control",O_RDWR|O_CLOEXEC);
    if(ctl<0) goto fail;
    idx=ioctl(ctl,LOOP_CTL_GET_FREE);
    if(idx<0) goto fail;
    close(ctl); ctl=-1;
    snprintf(loop_path,sizeof(loop_path),DDEV "/loop%d",idx);
    c->loop_fd=open(loop_path,O_RDWR|O_CLOEXEC);
    if(c->loop_fd<0) goto fail;
    loop.fd=(uint32_t)image;
    loop.info.lo_flags=LO_FLAGS_READ_ONLY|LO_FLAGS_AUTOCLEAR;
    if(ioctl(c->loop_fd,LOOP_CONFIGURE,&loop)) goto fail;
    why="FAIL_SQUASHFS";
    if(run_directory(DRUN "/dby-device") || mount(loop_path,DRUN "/dby-device","squashfs",MS_RDONLY|MS_NOSUID|MS_NODEV,NULL)) goto fail;
    c->image_mounted=1;
    close(image); image=-1;
    why="FAIL_MODULE_DIR";
    if(lstat(DMOD,&st)) {
        if(errno!=ENOENT || mkdir(DMOD,0755)) goto fail;
        c->module_created=1;
    } else if(!S_ISDIR(st.st_mode)) goto fail;
    why="FAIL_MODULE_BIND";
    if(mount(DRUN "/dby-device/modules/7.2.10",DMOD,NULL,MS_BIND,NULL)) goto fail;
    c->module_mounted=1;
    if(mount(NULL,DMOD,NULL,MS_BIND|MS_REMOUNT|MS_RDONLY|MS_NOSUID|MS_NODEV,NULL)) goto fail;
    why="FAIL_FIRMWARE_PATH";
    oldfd=open(DFW,O_RDONLY|O_CLOEXEC);
    if(oldfd<0) goto fail;
    n=read(oldfd,c->previous_fw,sizeof(c->previous_fw)-1); close(oldfd);
    if(n<0 || n>=(ssize_t)sizeof(c->previous_fw)-1) goto fail;
    c->previous_fw[n]=0;
    if(n && c->previous_fw[n-1]=='\n') c->previous_fw[n-1]=0;
    c->fw_changed=1;
    if(firmware_path("/run/dby-device/firmware",0)) goto fail;
    why="FAIL_CONFIG";
    for(i=0;i<DEVICE_CONFIG_COUNT;i++) if(config_copy(c,i)) goto fail;
    why="FAIL_STATUS";
    if(device_status("OK")) goto fail;
    close(c->loop_fd); c->loop_fd=-1; /* mount holds loop; autoclear after unmount */
    puts("DEVICE PACK OK");
    return "OK";
fail:
    if(image>=0) close(image);
    if(ctl>=0) close(ctl);
    if(device_release(c,1)) why="FAIL_CLEANUP";
    if(c->run_mounted) (void)device_status(why);
    puts(why); /* Constant status only, no device identity or configuration dump. */
    return why;
}

struct root_context {
    int mounted, moved, root_moved;
    int old_fd, target_fd, new_fd;
    struct device_context device;
};
static int wait_partition(void *unused)
{
    struct stat st;
    int i;
    (void)unused;
    for (i = 0; i <= 20; i++) {
        if (!stat("/dev/mmcblk0p2", &st) && S_ISBLK(st.st_mode)) break;
        if (i != 20) sleep(1);
    }
    show("/proc/partitions");
    return i > 20 ? -ENOENT : 0;
}
static int protect_p1(void *unused)
{
    (void)unused;
    if (readonly("/dev/mmcblk0p1")) return -errno;
    puts("p1 BLOCK READ-ONLY; p2 validation follows");
    return 0;
}
static int read_super(void *unused, unsigned char *sb)
{
    int fd, saved = 0;
    size_t done = 0;
    (void)unused;
    fd = open("/dev/mmcblk0p2", O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -errno;
    while (done < 1024) {
        ssize_t n = pread(fd, sb + done, 1024 - done, (off_t)(1024 + done));
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) { saved = n < 0 ? errno : EIO; break; }
        done += (size_t)n;
    }
    close(fd);
    return -saved;
}
static int mount_root(void *p)
{
    struct root_context *c = p;
    if (mkdir("/newroot", 0755) && errno != EEXIST) return -errno;
    c->old_fd = open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    c->target_fd = open("/newroot", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (c->old_fd < 0 || c->target_fd < 0) return -errno;
    if (mount("/dev/mmcblk0p2", "/newroot", "ext4", MS_NOATIME, "errors=remount-ro")) return -errno;
    c->mounted = 1;
    c->new_fd = open("/newroot", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (c->new_fd < 0) return -errno;
    puts("p2 EXT4 ubuntu-root MOUNTED READ-WRITE");
    return 0;
}
static int check_init(void *unused)
{
    struct stat st;
    (void)unused;
    if (stat("/newroot/sbin/init", &st)) return -errno;
    if (!S_ISREG(st.st_mode)) return -EINVAL;
    if (access("/newroot/sbin/init", X_OK)) return -errno;
    return 0;
}
static int prepare_device(void *p)
{
    struct root_context *c=p;
    (void)device_setup(&c->device);
    return 0;
}
static int switch_exec(void *p)
{
    struct root_context *c = p;
    const char *const names[] = {"dev", "proc", "sys"};
    char from[32], to[64];
    unsigned i;
    char *const args[] = {"/sbin/init", NULL};
    for (i = 0; i < 3; i++) {
        snprintf(to, sizeof(to), "/newroot/%s", names[i]);
        if (!directory(to)) return -ENOTDIR; /* Never create p2 API directories. */
        snprintf(from, sizeof(from), "/%s", names[i]);
        if (mount(from, to, NULL, MS_MOVE, NULL)) return -errno;
        c->moved |= 1 << i;
    }
    if (chdir("/newroot")) return -errno;
    if (mount(".", "/", NULL, MS_MOVE, NULL)) return -errno;
    c->root_moved = 1;
    if (chroot(".") || chdir("/")) return -errno;
    /* The runtime policy must exist before system udev coldplug starts. */
    (void)safe_mode_apply("/run/dby-p1/dby-seed/seed.conf", "/run/modprobe.d");
    execv("/sbin/init", args);
    return -errno;
}
static int cleanup_root(void *p)
{
    struct root_context *c = p;
    const char *const names[] = {"dev", "proc", "sys"};
    char from[64], to[32];
    int saved = 0, i;
    /* Roll back the moved root without depending on /proc after a chroot. */
    if (c->root_moved) {
        if (syscall(SYS_move_mount, c->new_fd, "", c->target_fd, "",
                    MOVE_MOUNT_F_EMPTY_PATH | MOVE_MOUNT_T_EMPTY_PATH)) return -errno;
        c->root_moved = 0;
    }
    if (c->old_fd >= 0 && (fchdir(c->old_fd) || chroot(".") || chdir("/"))) return -errno;
    for (i = 2; i >= 0; i--) if (c->moved & (1 << i)) {
        snprintf(from, sizeof(from), "/newroot/%s", names[i]);
        snprintf(to, sizeof(to), "/%s", names[i]);
        if (mount(from, to, NULL, MS_MOVE, NULL)) { if (!saved) saved = errno; }
        else c->moved &= ~(1 << i);
    }
    device_release(&c->device, 0);
    if (c->new_fd >= 0) { close(c->new_fd); c->new_fd = -1; }
    if (c->target_fd >= 0) { close(c->target_fd); c->target_fd = -1; }
    if (c->old_fd >= 0) { close(c->old_fd); c->old_fd = -1; }
    if (c->mounted) {
        if (umount("/newroot")) {
            if (!saved) saved = errno;
            /* Failure is explicit; make the still-mounted p2 read-only if possible. */
            if (mount(NULL, "/newroot", NULL, MS_REMOUNT | MS_RDONLY, NULL)) perror("p2 fallback remount-ro");
        } else { c->mounted = 0; puts("p2 UNMOUNTED BEFORE FALLBACK"); }
    }
    return -saved;
}
int main(int argc, char **argv)
{
    struct utsname u;
    struct root_context c = {.old_fd = -1, .target_fd = -1, .new_fd = -1, .device = {.loop_fd = -1}};
    const struct root_ops ops = {wait_partition, protect_p1, read_super, mount_root, check_init, prepare_device, switch_exec, cleanup_root};
    const char *why = "unknown";
    int fd, rc, i;
    char line[256];
    FILE *f;
    (void)argc; (void)argv;
    mkdir("/proc", 0555); mkdir("/sys", 0555); mkdir("/dev", 0755); mkdir("/sd", 0555);
    mount("proc", "/proc", "proc", MS_NOSUID | MS_NODEV | MS_NOEXEC, NULL);
    mount("sysfs", "/sys", "sysfs", MS_NOSUID | MS_NODEV | MS_NOEXEC, NULL);
    mount("devtmpfs", "/dev", "devtmpfs", MS_NOSUID | MS_NOEXEC, NULL);
    fd = open("/dev/console", O_RDWR);
    if (fd >= 0) { dup2(fd, 0); dup2(fd, 1); dup2(fd, 2); if (fd > 2) close(fd); }
    setvbuf(stdout, NULL, _IONBF, 0);
    puts("DBY LINUX INIT");
    if (!uname(&u)) printf("uname: %s %s %s %s\n", u.sysname, u.release, u.version, u.machine);
    show("/proc/cmdline");
    f = fopen("/proc/meminfo", "r");
    if (f) { while (fgets(line, sizeof(line), f)) if (!strncmp(line, "MemTotal:", 9)) { fputs(line, stdout); break; } fclose(f); }
    rc = root_attempt(&ops, &c, &why);
    printf("UBUNTU FALLBACK: %s; errno=%d (%s)\n", why, -rc, strerror(-rc));
    show_kernel_log();
    for (i = 150; i > 0; i--) { printf("\rREBOOT IN %3d s", i); sleep(1); }
    putchar('\n');
    sync();
    if (reboot(RB_AUTOBOOT)) perror("reboot");
    for (;;) pause();
}
