// SPDX-License-Identifier: GPL-2.0-only
#ifndef DBY_SAFE_MODE_H
#define DBY_SAFE_MODE_H
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>

#define SEED_LIMIT (64 * 1024)

static char *seed_trim(char *text)
{
    char *end;
    while (isspace((unsigned char)*text)) text++;
    end = text + strlen(text);
    while (end > text && isspace((unsigned char)end[-1])) *--end = 0;
    return text;
}

/* Scan one bounded file in memory. Ignore unrelated keys, never evaluate them.
 * Unknown/malformed lines are skipped; the last safe_mode key wins. NUL,
 * control bytes and oversized input mean false. Never prevent system init. */
static int safe_mode_parse(const char *data, size_t size)
{
    char *copy, *line, *next, *key, *value, *equals;
    size_t i, length;
    int system = 0, enabled = 0;
    if (size > SEED_LIMIT) return 0;
    for (i = 0; i < size; i++)
        if (!data[i] || (unsigned char)data[i] == 127 || ((unsigned char)data[i] < 32 &&
            data[i] != '\n' && data[i] != '\r' && data[i] != '\t')) return 0;
    copy = malloc(size + 1);
    if (!copy) return 0;
    memcpy(copy, data, size); copy[size] = 0;
    for (line = copy; line; line = next) {
        next = strchr(line, '\n');
        if (next) *next++ = 0;
        key = seed_trim(line);
        if (!*key || *key == '#' || *key == ';') continue;
        length = strlen(key);
        if (*key == '[') {
            if (length < 3 || key[length - 1] != ']') continue;
            key[length - 1] = 0;
            key = seed_trim(key + 1);
            if (!*key || strchr(key, '[') || strchr(key, ']')) continue;
            system = !strcasecmp(key, "system");
            continue;
        }
        equals = strchr(key, '=');
        if (!equals) continue;
        *equals = 0;
        value = seed_trim(equals + 1); key = seed_trim(key);
        if (!*key) continue;
        if (system && !strcasecmp(key, "safe_mode")) {
            enabled = !strcasecmp(value, "true");
        }
    }
    free(copy);
    return enabled;
}

/* Called after chroot and before exec /sbin/init. The device-pack setup has
 * mounted p1 read-only at /run/dby-p1 and /run is tmpfs. Only a runtime kmod
 * policy is written: system udev does the loading, not this initramfs. The
 * seed applier must not persist another blocking policy on the root volume.
 * Missing, malformed or unreadable input and write errors never block boot. */
static int safe_mode_apply(const char *seed, const char *directory)
{
    static const char policy[] =
        "# Managed runtime safe mode; recreated each boot.\n"
        "blacklist msm\ninstall msm /bin/false\n"
        "blacklist nt36523_thp\ninstall nt36523_thp /bin/false\n"
        "blacklist panel_novatek_nt36523\ninstall panel_novatek_nt36523 /bin/false\n";
    struct stat info;
    char *buffer = NULL, path[1024];
    ssize_t got;
    size_t used = 0, at = 0;
    int input = -1, output = -1, enabled = 0, result = 0;
    input = open(seed, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    if (input < 0) goto cleanup;
    if (fstat(input, &info) || !S_ISREG(info.st_mode) || info.st_size < 0 ||
        info.st_size > SEED_LIMIT) goto cleanup;
    buffer = malloc(SEED_LIMIT + 1);
    if (!buffer) goto cleanup;
    while (used <= SEED_LIMIT) {
        got = read(input, buffer + used, SEED_LIMIT + 1 - used);
        if (got < 0) { if (errno == EINTR) continue; goto cleanup; }
        if (!got) break;
        used += (size_t)got;
    }
    enabled = safe_mode_parse(buffer, used);
cleanup:
    if (input >= 0 && close(input)) enabled = 0;
    free(buffer);
    if (!enabled) return 0;
    if (mkdir(directory, 0755) && errno != EEXIST) return -1;
    if (lstat(directory, &info) || !S_ISDIR(info.st_mode)) return -1;
    if (snprintf(path, sizeof(path), "%s/dby-safe-mode.conf", directory) >= (int)sizeof(path)) return -1;
    output = open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0644);
    if (output < 0) return -1;
    while (at < sizeof(policy) - 1) {
        got = write(output, policy + at, sizeof(policy) - 1 - at);
        if (got < 0 && errno == EINTR) continue;
        if (got <= 0) { result = -1; break; }
        at += (size_t)got;
    }
    if (fchmod(output, 0644)) result = -1;
    if (close(output)) result = -1;
    if (result) { (void)unlink(path); return -1; }
    puts("SAFE MODE: runtime module policy installed");
    return 1;
}
#endif
