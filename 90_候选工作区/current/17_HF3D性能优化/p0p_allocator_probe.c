#define _GNU_SOURCE

#include <dlfcn.h>
#include <errno.h>
#include <malloc.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

typedef void (*observer_action)(void);

static pthread_barrier_t ready_barrier;
static pthread_barrier_t finish_barrier;
static pthread_barrier_t snapshot_barrier;

static void touch(void *pointer, size_t size, unsigned char value) {
    if (pointer == NULL) {
        fprintf(stderr, "allocation unexpectedly failed\n");
        exit(91);
    }
    memset(pointer, value, size);
}

static void *worker(void *raw_index) {
    uintptr_t index = (uintptr_t)raw_index;
    (void)pthread_barrier_wait(&ready_barrier);
    void *first = malloc(32U);
    touch(first, 32U, (unsigned char)(index + 1U));
    void *second = calloc(1U, 48U);
    touch(second, 48U, (unsigned char)(index + 2U));
    free(second);
    free(first);
    (void)pthread_barrier_wait(&finish_barrier);
    (void)pthread_barrier_wait(&snapshot_barrier);
    return NULL;
}

static observer_action resolve_action(const char *name) {
    dlerror();
    observer_action action = (observer_action)dlsym(RTLD_DEFAULT, name);
    const char *error = dlerror();
    if (error != NULL || action == NULL) {
        fprintf(stderr, "observer action unavailable: %s\n", name);
        exit(92);
    }
    return action;
}

static void known_sequence(observer_action start, observer_action stop) {
    void *pre_capture_free = malloc(19U);
    void *pre_capture_realloc = malloc(23U);
    touch(pre_capture_free, 19U, 10U);
    touch(pre_capture_realloc, 23U, 11U);
    pthread_t first_thread;
    pthread_t second_thread;
    if (pthread_barrier_init(&ready_barrier, NULL, 3U) != 0 ||
        pthread_barrier_init(&finish_barrier, NULL, 3U) != 0 ||
        pthread_barrier_init(&snapshot_barrier, NULL, 3U) != 0 ||
        pthread_create(&first_thread, NULL, worker, (void *)(uintptr_t)0U) != 0 ||
        pthread_create(&second_thread, NULL, worker, (void *)(uintptr_t)1U) != 0) {
        fprintf(stderr, "thread setup failed\n");
        exit(93);
    }
    start();
    (void)pthread_barrier_wait(&ready_barrier);

    void *plain = malloc(10U);
    touch(plain, 10U, 3U);
    void *zeroed = calloc(2U, 20U);
    touch(zeroed, 40U, 4U);
    plain = realloc(plain, 100U);
    touch(plain, 100U, 5U);

    void *posix_pointer = NULL;
    if (posix_memalign(&posix_pointer, 64U, 128U) != 0) {
        fprintf(stderr, "posix_memalign failed\n");
        exit(94);
    }
    touch(posix_pointer, 128U, 6U);
    void *invalid_posix_pointer = (void *)(uintptr_t)1U;
    errno = EDOM;
    int invalid_status =
        posix_memalign(&invalid_posix_pointer, 3U, 17U);
    if (invalid_status != EINVAL ||
        invalid_posix_pointer != (void *)(uintptr_t)1U || errno != EDOM) {
        fprintf(stderr, "posix_memalign failure semantics changed\n");
        exit(98);
    }
    void *aligned_pointer = aligned_alloc(64U, 128U);
    touch(aligned_pointer, 128U, 7U);
    void *memalign_pointer = memalign(32U, 64U);
    touch(memalign_pointer, 64U, 8U);
    void *valloc_pointer = valloc(33U);
    touch(valloc_pointer, 33U, 12U);
    void *pvalloc_pointer = pvalloc(65U);
    touch(pvalloc_pointer, 65U, 13U);

    pre_capture_realloc = realloc(pre_capture_realloc, 77U);
    touch(pre_capture_realloc, 77U, 14U);

    long page_raw = sysconf(_SC_PAGESIZE);
    size_t page = page_raw > 0 ? (size_t)page_raw : 4096U;
    void *mapped = mmap(NULL, 3U * page, PROT_READ | PROT_WRITE,
                        MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mapped == MAP_FAILED) {
        fprintf(stderr, "mmap failed: %s\n", strerror(errno));
        exit(95);
    }
    touch(mapped, 3U * page, 9U);
    void *mapped64 = mmap64(NULL, page, PROT_READ | PROT_WRITE,
                            MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mapped64 == MAP_FAILED) {
        fprintf(stderr, "mmap64 failed: %s\n", strerror(errno));
        exit(99);
    }
    touch(mapped64, page, 15U);

    (void)pthread_barrier_wait(&finish_barrier);
    free(memalign_pointer);
    free(aligned_pointer);
    free(posix_pointer);
    free(pvalloc_pointer);
    free(valloc_pointer);
    free(zeroed);
    free(plain);
    free(pre_capture_realloc);
    free(pre_capture_free);
    if (munmap((char *)mapped + page, page) != 0 ||
        munmap(mapped, page) != 0 ||
        munmap((char *)mapped + 2U * page, page) != 0 ||
        munmap(mapped64, page) != 0) {
        fprintf(stderr, "partial munmap sequence failed\n");
        exit(96);
    }
    stop();
    (void)pthread_barrier_wait(&snapshot_barrier);
    (void)pthread_join(first_thread, NULL);
    (void)pthread_join(second_thread, NULL);
    (void)pthread_barrier_destroy(&ready_barrier);
    (void)pthread_barrier_destroy(&finish_barrier);
    (void)pthread_barrier_destroy(&snapshot_barrier);
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: p0p_allocator_probe MODE\n");
        return 64;
    }
    observer_action start = resolve_action("p0p_observer_reset_and_start");
    observer_action stop = resolve_action("p0p_observer_stop_and_write");
    if (strcmp(argv[1], "known") == 0 || strcmp(argv[1], "exit7") == 0) {
        known_sequence(start, stop);
        return strcmp(argv[1], "exit7") == 0 ? 7 : 0;
    }
    if (strcmp(argv[1], "overflow") == 0) {
        start();
        volatile size_t huge = SIZE_MAX;
        void *pointer = calloc(huge, 2U);
        if (pointer != NULL) {
            free(pointer);
            fprintf(stderr, "overflow allocation unexpectedly succeeded\n");
            return 97;
        }
        stop();
        return 0;
    }
    fprintf(stderr, "unknown probe mode\n");
    return 65;
}
