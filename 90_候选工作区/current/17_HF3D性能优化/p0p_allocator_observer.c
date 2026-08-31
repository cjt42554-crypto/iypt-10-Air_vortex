#define _GNU_SOURCE

#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <malloc.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>

extern void *__libc_malloc(size_t size);
extern void *__libc_calloc(size_t count, size_t size);
extern void *__libc_realloc(void *pointer, size_t size);
extern void __libc_free(void *pointer);
extern void *__libc_memalign(size_t alignment, size_t size);
extern void *__mmap(void *address, size_t length, int protection, int flags,
                    int descriptor, off_t offset) __attribute__((weak));
extern void *__mmap64(void *address, size_t length, int protection, int flags,
                      int descriptor, off64_t offset) __attribute__((weak));
extern int __munmap(void *address, size_t length) __attribute__((weak));
extern char **environ;

typedef struct {
    atomic_ullong calls;
    atomic_ullong requested_bytes;
    atomic_ullong actual_bytes;
} api_counter;

typedef struct {
    api_counter malloc_api;
    api_counter calloc_api;
    api_counter realloc_api;
    api_counter posix_memalign_api;
    api_counter aligned_alloc_api;
    api_counter memalign_api;
    api_counter valloc_api;
    api_counter pvalloc_api;
    api_counter mmap_api;
    api_counter free_api;
    api_counter munmap_api;
    atomic_ullong heap_live_bytes;
    atomic_ullong mmap_live_bytes;
    atomic_ullong combined_peak_live_bytes;
    atomic_ullong heap_peak_live_bytes;
    atomic_ullong mmap_peak_live_bytes;
    atomic_ullong overflow_events;
    atomic_ullong accounting_underflow_events;
    atomic_ullong hook_reentry_events;
    atomic_ullong tracking_table_overflow_events;
    atomic_ullong tracking_consistency_events;
    atomic_ullong untracked_free_events;
    atomic_ullong untracked_realloc_events;
    atomic_ullong untracked_munmap_events;
    atomic_ullong heap_live_allocations;
    atomic_ullong heap_peak_live_allocations;
    atomic_ullong mmap_live_mappings;
    atomic_ullong mmap_peak_live_mappings;
} observer_counters;

#define HEAP_TRACK_CAPACITY (1U << 20)
#define MMAP_TRACK_CAPACITY (1U << 16)

typedef struct {
    void *pointer;
    size_t actual_bytes;
    unsigned char state;
} heap_record;

typedef struct {
    uintptr_t start;
    size_t length;
    bool occupied;
} mmap_record;

static observer_counters counters;
static heap_record heap_records[HEAP_TRACK_CAPACITY];
static mmap_record mmap_records[MMAP_TRACK_CAPACITY];
static atomic_flag tracking_lock = ATOMIC_FLAG_INIT;
static atomic_bool capture_enabled = false;
static atomic_bool snapshot_written = false;
static atomic_bool capture_started_once = false;
static atomic_ullong active_recorders = 0;
static atomic_bool public_symbols_resolved = false;
static __thread unsigned int hook_depth = 0;
static int output_fd = -1;
static size_t observer_page_size = 4096U;
static int (*real_posix_memalign)(void **, size_t, size_t) = NULL;
static void *(*real_aligned_alloc)(size_t, size_t) = NULL;

static void acquire_tracking_lock(void) {
    while (atomic_flag_test_and_set_explicit(&tracking_lock,
                                              memory_order_acquire)) {
    }
}

static void release_tracking_lock(void) {
    atomic_flag_clear_explicit(&tracking_lock, memory_order_release);
}

static void wait_for_active_recorders(void) {
    while (atomic_load_explicit(&active_recorders, memory_order_acquire) != 0U) {
        (void)syscall(SYS_sched_yield);
    }
}

static void mark_overflow(void) {
    atomic_fetch_add_explicit(&counters.overflow_events, 1,
                              memory_order_relaxed);
}

static void add_saturating(atomic_ullong *target, unsigned long long value) {
    unsigned long long current = atomic_load_explicit(target, memory_order_relaxed);
    for (;;) {
        unsigned long long next;
        if (ULLONG_MAX - current < value) {
            next = ULLONG_MAX;
            mark_overflow();
        } else {
            next = current + value;
        }
        if (atomic_compare_exchange_weak_explicit(
                target, &current, next, memory_order_relaxed,
                memory_order_relaxed)) {
            return;
        }
    }
}

static void update_peak(atomic_ullong *peak, unsigned long long value) {
    unsigned long long current = atomic_load_explicit(peak, memory_order_relaxed);
    while (current < value &&
           !atomic_compare_exchange_weak_explicit(
               peak, &current, value, memory_order_relaxed,
               memory_order_relaxed)) {
    }
}

static void update_combined_peak(void) {
    unsigned long long heap =
        atomic_load_explicit(&counters.heap_live_bytes, memory_order_relaxed);
    unsigned long long mapped =
        atomic_load_explicit(&counters.mmap_live_bytes, memory_order_relaxed);
    unsigned long long total =
        ULLONG_MAX - heap < mapped ? ULLONG_MAX : heap + mapped;
    if (total == ULLONG_MAX && mapped != ULLONG_MAX - heap) {
        mark_overflow();
    }
    update_peak(&counters.combined_peak_live_bytes, total);
}

static void add_live(atomic_ullong *current, atomic_ullong *peak,
                     unsigned long long value) {
    unsigned long long previous =
        atomic_fetch_add_explicit(current, value, memory_order_relaxed);
    if (ULLONG_MAX - previous < value) {
        atomic_store_explicit(current, ULLONG_MAX, memory_order_relaxed);
        mark_overflow();
        update_peak(peak, ULLONG_MAX);
    } else {
        update_peak(peak, previous + value);
    }
    update_combined_peak();
}

static void remove_live(atomic_ullong *current, unsigned long long value) {
    unsigned long long previous =
        atomic_fetch_sub_explicit(current, value, memory_order_relaxed);
    if (previous < value) {
        atomic_store_explicit(current, 0, memory_order_relaxed);
        atomic_fetch_add_explicit(&counters.accounting_underflow_events, 1,
                                  memory_order_relaxed);
    }
}

static size_t pointer_hash(void *pointer) {
    uintptr_t value = (uintptr_t)pointer >> 4U;
    value ^= value >> 33U;
    value *= UINT64_C(0xff51afd7ed558ccd);
    value ^= value >> 33U;
    return (size_t)value & (HEAP_TRACK_CAPACITY - 1U);
}

static bool heap_lookup_locked(void *pointer, size_t *actual_bytes) {
    size_t index = pointer_hash(pointer);
    for (size_t probe = 0; probe < HEAP_TRACK_CAPACITY; ++probe) {
        heap_record *record = &heap_records[(index + probe) &
                                            (HEAP_TRACK_CAPACITY - 1U)];
        if (record->state == 0U) {
            return false;
        }
        if (record->state == 1U && record->pointer == pointer) {
            *actual_bytes = record->actual_bytes;
            return true;
        }
    }
    return false;
}

static bool heap_insert_locked(void *pointer, size_t actual_bytes) {
    size_t index = pointer_hash(pointer);
    size_t tombstone = HEAP_TRACK_CAPACITY;
    for (size_t probe = 0; probe < HEAP_TRACK_CAPACITY; ++probe) {
        size_t slot = (index + probe) & (HEAP_TRACK_CAPACITY - 1U);
        heap_record *record = &heap_records[slot];
        if (record->state == 1U && record->pointer == pointer) {
            atomic_fetch_add_explicit(&counters.tracking_consistency_events, 1,
                                      memory_order_relaxed);
            return false;
        }
        if (record->state == 2U && tombstone == HEAP_TRACK_CAPACITY) {
            tombstone = slot;
        }
        if (record->state == 0U) {
            size_t target = tombstone == HEAP_TRACK_CAPACITY ? slot : tombstone;
            heap_records[target].pointer = pointer;
            heap_records[target].actual_bytes = actual_bytes;
            heap_records[target].state = 1U;
            unsigned long long live = atomic_fetch_add_explicit(
                                               &counters.heap_live_allocations,
                                               1, memory_order_relaxed) +
                                           1U;
            update_peak(&counters.heap_peak_live_allocations, live);
            return true;
        }
    }
    if (tombstone != HEAP_TRACK_CAPACITY) {
        heap_records[tombstone].pointer = pointer;
        heap_records[tombstone].actual_bytes = actual_bytes;
        heap_records[tombstone].state = 1U;
        unsigned long long live =
            atomic_fetch_add_explicit(&counters.heap_live_allocations, 1,
                                      memory_order_relaxed) +
            1U;
        update_peak(&counters.heap_peak_live_allocations, live);
        return true;
    }
    atomic_fetch_add_explicit(&counters.tracking_table_overflow_events, 1,
                              memory_order_relaxed);
    return false;
}

static bool heap_remove_locked(void *pointer, size_t *actual_bytes) {
    size_t index = pointer_hash(pointer);
    for (size_t probe = 0; probe < HEAP_TRACK_CAPACITY; ++probe) {
        heap_record *record = &heap_records[(index + probe) &
                                            (HEAP_TRACK_CAPACITY - 1U)];
        if (record->state == 0U) {
            return false;
        }
        if (record->state == 1U && record->pointer == pointer) {
            *actual_bytes = record->actual_bytes;
            record->pointer = NULL;
            record->actual_bytes = 0U;
            record->state = 2U;
            unsigned long long previous = atomic_fetch_sub_explicit(
                &counters.heap_live_allocations, 1, memory_order_relaxed);
            if (previous == 0U) {
                atomic_store_explicit(&counters.heap_live_allocations, 0,
                                      memory_order_relaxed);
                atomic_fetch_add_explicit(
                    &counters.tracking_consistency_events, 1,
                    memory_order_relaxed);
            }
            return true;
        }
    }
    return false;
}

static bool mmap_insert_locked(uintptr_t start, size_t length) {
    for (size_t index = 0; index < MMAP_TRACK_CAPACITY; ++index) {
        if (!mmap_records[index].occupied) {
            mmap_records[index].start = start;
            mmap_records[index].length = length;
            mmap_records[index].occupied = true;
            unsigned long long live = atomic_fetch_add_explicit(
                                               &counters.mmap_live_mappings, 1,
                                               memory_order_relaxed) +
                                           1U;
            update_peak(&counters.mmap_peak_live_mappings, live);
            return true;
        }
    }
    atomic_fetch_add_explicit(&counters.tracking_table_overflow_events, 1,
                              memory_order_relaxed);
    return false;
}

static void mmap_remove_record_locked(size_t index) {
    mmap_records[index].start = 0U;
    mmap_records[index].length = 0U;
    mmap_records[index].occupied = false;
    unsigned long long previous = atomic_fetch_sub_explicit(
        &counters.mmap_live_mappings, 1, memory_order_relaxed);
    if (previous == 0U) {
        atomic_store_explicit(&counters.mmap_live_mappings, 0,
                              memory_order_relaxed);
        atomic_fetch_add_explicit(&counters.tracking_consistency_events, 1,
                                  memory_order_relaxed);
    }
}

static unsigned long long mmap_untrack_range_locked(uintptr_t start,
                                                     size_t length) {
    if (length == 0U || start > UINTPTR_MAX - length) {
        mark_overflow();
        return 0U;
    }
    uintptr_t end = start + length;
    unsigned long long removed = 0U;
    for (size_t index = 0; index < MMAP_TRACK_CAPACITY; ++index) {
        mmap_record *record = &mmap_records[index];
        if (!record->occupied || record->start > UINTPTR_MAX - record->length) {
            continue;
        }
        uintptr_t record_end = record->start + record->length;
        uintptr_t overlap_start = record->start > start ? record->start : start;
        uintptr_t overlap_end = record_end < end ? record_end : end;
        if (overlap_start >= overlap_end) {
            continue;
        }
        size_t overlap = (size_t)(overlap_end - overlap_start);
        if (ULLONG_MAX - removed < (unsigned long long)overlap) {
            removed = ULLONG_MAX;
            mark_overflow();
        } else {
            removed += (unsigned long long)overlap;
        }
        if (overlap_start == record->start && overlap_end == record_end) {
            mmap_remove_record_locked(index);
        } else if (overlap_start == record->start) {
            record->start = overlap_end;
            record->length = (size_t)(record_end - overlap_end);
        } else if (overlap_end == record_end) {
            record->length = (size_t)(overlap_start - record->start);
        } else {
            size_t right_length = (size_t)(record_end - overlap_end);
            record->length = (size_t)(overlap_start - record->start);
            if (!mmap_insert_locked(overlap_end, right_length)) {
                atomic_fetch_add_explicit(
                    &counters.tracking_consistency_events, 1,
                    memory_order_relaxed);
            }
        }
    }
    return removed;
}

static bool begin_recording(void) {
    if (hook_depth != 1U) {
        if (atomic_load_explicit(&capture_enabled, memory_order_acquire)) {
            atomic_fetch_add_explicit(&counters.hook_reentry_events, 1,
                                      memory_order_relaxed);
        }
        return false;
    }
    if (!atomic_load_explicit(&capture_enabled, memory_order_acquire)) {
        return false;
    }
    atomic_fetch_add_explicit(&active_recorders, 1, memory_order_acq_rel);
    if (!atomic_load_explicit(&capture_enabled, memory_order_acquire)) {
        atomic_fetch_sub_explicit(&active_recorders, 1, memory_order_release);
        return false;
    }
    return true;
}

static void end_recording(void) {
    unsigned long long previous = atomic_fetch_sub_explicit(
        &active_recorders, 1, memory_order_release);
    if (previous == 0U) {
        atomic_store_explicit(&active_recorders, 0, memory_order_relaxed);
        atomic_fetch_add_explicit(&counters.tracking_consistency_events, 1,
                                  memory_order_relaxed);
    }
}

static bool rounded_mapping_length(size_t requested, size_t *actual) {
    if (requested == 0U || observer_page_size == 0U ||
        requested > SIZE_MAX - (observer_page_size - 1U)) {
        mark_overflow();
        return false;
    }
    *actual = ((requested + observer_page_size - 1U) / observer_page_size) *
              observer_page_size;
    return true;
}

static void count_allocation(api_counter *api, size_t requested, void *pointer) {
    atomic_fetch_add_explicit(&api->calls, 1, memory_order_relaxed);
    add_saturating(&api->requested_bytes, (unsigned long long)requested);
    if (pointer != NULL) {
        size_t actual = malloc_usable_size(pointer);
        add_saturating(&api->actual_bytes, (unsigned long long)actual);
        acquire_tracking_lock();
        if (heap_insert_locked(pointer, actual)) {
            add_live(&counters.heap_live_bytes, &counters.heap_peak_live_bytes,
                     (unsigned long long)actual);
        }
        release_tracking_lock();
    }
}

static void count_free(void *pointer) {
    atomic_fetch_add_explicit(&counters.free_api.calls, 1,
                              memory_order_relaxed);
    if (pointer != NULL) {
        size_t actual = 0U;
        acquire_tracking_lock();
        bool tracked = heap_remove_locked(pointer, &actual);
        if (tracked) {
            remove_live(&counters.heap_live_bytes, (unsigned long long)actual);
        } else {
            atomic_fetch_add_explicit(&counters.untracked_free_events, 1,
                                      memory_order_relaxed);
        }
        release_tracking_lock();
        if (tracked) {
            add_saturating(&counters.free_api.actual_bytes,
                           (unsigned long long)actual);
        }
    }
}

void *malloc(size_t size) {
    hook_depth++;
    bool active = begin_recording();
    void *result = __libc_malloc(size);
    if (active) {
        count_allocation(&counters.malloc_api, size, result);
        end_recording();
    }
    hook_depth--;
    return result;
}

void *calloc(size_t count, size_t size) {
    hook_depth++;
    bool active = begin_recording();
    bool overflow = size != 0 && count > SIZE_MAX / size;
    size_t requested = overflow ? SIZE_MAX : count * size;
    void *result = __libc_calloc(count, size);
    if (active) {
        if (overflow) {
            mark_overflow();
        }
        count_allocation(&counters.calloc_api, requested, result);
        end_recording();
    }
    hook_depth--;
    return result;
}

void *realloc(void *pointer, size_t size) {
    hook_depth++;
    bool active = begin_recording();
    size_t old_actual = 0U;
    bool old_tracked = false;
    if (active && pointer != NULL) {
        acquire_tracking_lock();
        old_tracked = heap_lookup_locked(pointer, &old_actual);
        release_tracking_lock();
        if (!old_tracked) {
            atomic_fetch_add_explicit(&counters.untracked_realloc_events, 1,
                                      memory_order_relaxed);
        }
    }
    void *result = __libc_realloc(pointer, size);
    if (active) {
        atomic_fetch_add_explicit(&counters.realloc_api.calls, 1,
                                  memory_order_relaxed);
        add_saturating(&counters.realloc_api.requested_bytes,
                       (unsigned long long)size);
        if (result != NULL) {
            size_t actual = malloc_usable_size(result);
            add_saturating(&counters.realloc_api.actual_bytes,
                           (unsigned long long)actual);
            acquire_tracking_lock();
            if (old_tracked) {
                size_t removed = 0U;
                if (heap_remove_locked(pointer, &removed)) {
                    if (removed != old_actual) {
                        atomic_fetch_add_explicit(
                            &counters.tracking_consistency_events, 1,
                            memory_order_relaxed);
                    }
                    remove_live(&counters.heap_live_bytes,
                                (unsigned long long)removed);
                } else {
                    atomic_fetch_add_explicit(
                        &counters.tracking_consistency_events, 1,
                        memory_order_relaxed);
                }
            }
            if (heap_insert_locked(result, actual)) {
                add_live(&counters.heap_live_bytes,
                         &counters.heap_peak_live_bytes,
                         (unsigned long long)actual);
            }
            release_tracking_lock();
        } else if (old_tracked && size == 0U) {
            acquire_tracking_lock();
            size_t removed = 0U;
            if (heap_remove_locked(pointer, &removed)) {
                if (removed != old_actual) {
                    atomic_fetch_add_explicit(
                        &counters.tracking_consistency_events, 1,
                        memory_order_relaxed);
                }
                remove_live(&counters.heap_live_bytes,
                            (unsigned long long)removed);
            } else {
                atomic_fetch_add_explicit(
                    &counters.tracking_consistency_events, 1,
                    memory_order_relaxed);
            }
            release_tracking_lock();
        }
        end_recording();
    }
    hook_depth--;
    return result;
}

void free(void *pointer) {
    hook_depth++;
    bool active = begin_recording();
    if (active) {
        count_free(pointer);
    }
    __libc_free(pointer);
    if (active) {
        end_recording();
    }
    hook_depth--;
}

int posix_memalign(void **result, size_t alignment, size_t size) {
    hook_depth++;
    bool active = begin_recording();
    int saved_errno = errno;
    void *pointer = NULL;
    int status;
    if (real_posix_memalign != NULL) {
        status = real_posix_memalign(&pointer, alignment, size);
    } else {
        bool valid_alignment = alignment >= sizeof(void *) &&
                               alignment % sizeof(void *) == 0U &&
                               (alignment & (alignment - 1U)) == 0U;
        pointer = valid_alignment ? __libc_memalign(alignment, size) : NULL;
        status = !valid_alignment ? EINVAL : (pointer == NULL ? ENOMEM : 0);
    }
    if (active) {
        count_allocation(&counters.posix_memalign_api, size, pointer);
        end_recording();
    }
    if (status == 0) {
        *result = pointer;
    }
    errno = saved_errno;
    hook_depth--;
    return status;
}

void *aligned_alloc(size_t alignment, size_t size) {
    hook_depth++;
    bool active = begin_recording();
    void *result = real_aligned_alloc != NULL
                       ? real_aligned_alloc(alignment, size)
                       : __libc_memalign(alignment, size);
    if (active) {
        count_allocation(&counters.aligned_alloc_api, size, result);
        end_recording();
    }
    hook_depth--;
    return result;
}

void *memalign(size_t alignment, size_t size) {
    hook_depth++;
    bool active = begin_recording();
    void *result = __libc_memalign(alignment, size);
    if (active) {
        count_allocation(&counters.memalign_api, size, result);
        end_recording();
    }
    hook_depth--;
    return result;
}

void *valloc(size_t size) {
    hook_depth++;
    bool active = begin_recording();
    void *result = __libc_memalign(observer_page_size, size);
    if (active) {
        count_allocation(&counters.valloc_api, size, result);
        end_recording();
    }
    hook_depth--;
    return result;
}

void *pvalloc(size_t size) {
    hook_depth++;
    bool active = begin_recording();
    size_t page = observer_page_size;
    size_t rounded;
    if (size > SIZE_MAX - (page - 1U)) {
        errno = ENOMEM;
        rounded = SIZE_MAX;
    } else {
        rounded = (size + page - 1U) & ~(page - 1U);
    }
    void *result =
        rounded == SIZE_MAX ? NULL : __libc_memalign(page, rounded);
    if (active) {
        if (rounded == SIZE_MAX) {
            mark_overflow();
        }
        count_allocation(&counters.pvalloc_api, size, result);
        end_recording();
    }
    hook_depth--;
    return result;
}

static void count_mmap(size_t requested, int flags, void *result) {
    atomic_fetch_add_explicit(&counters.mmap_api.calls, 1,
                              memory_order_relaxed);
    add_saturating(&counters.mmap_api.requested_bytes,
                   (unsigned long long)requested);
    if (result == MAP_FAILED) {
        return;
    }
    size_t actual = 0U;
    if (!rounded_mapping_length(requested, &actual)) {
        atomic_fetch_add_explicit(&counters.tracking_consistency_events, 1,
                                  memory_order_relaxed);
        return;
    }
    add_saturating(&counters.mmap_api.actual_bytes,
                   (unsigned long long)actual);
    acquire_tracking_lock();
    if ((flags & MAP_FIXED) != 0) {
        unsigned long long replaced =
            mmap_untrack_range_locked((uintptr_t)result, actual);
        if (replaced > 0U) {
            remove_live(&counters.mmap_live_bytes, replaced);
        }
    }
    if (mmap_insert_locked((uintptr_t)result, actual)) {
        add_live(&counters.mmap_live_bytes, &counters.mmap_peak_live_bytes,
                 (unsigned long long)actual);
    }
    release_tracking_lock();
}

void *mmap(void *address, size_t length, int protection, int flags,
           int descriptor, off_t offset) {
    hook_depth++;
    bool active = begin_recording();
    void *result = __mmap != NULL
                       ? __mmap(address, length, protection, flags, descriptor,
                                offset)
                       : (void *)syscall(SYS_mmap, address, length, protection,
                                         flags, descriptor, offset);
    if (active) {
        count_mmap(length, flags, result);
        end_recording();
    }
    hook_depth--;
    return result;
}

void *mmap64(void *address, size_t length, int protection, int flags,
             int descriptor, off64_t offset) {
    hook_depth++;
    bool active = begin_recording();
    void *result;
    if (__mmap64 != NULL) {
        result = __mmap64(address, length, protection, flags, descriptor,
                          offset);
    } else if (sizeof(off_t) == sizeof(off64_t)) {
        result = __mmap != NULL
                     ? __mmap(address, length, protection, flags, descriptor,
                              (off_t)offset)
                     : (void *)syscall(SYS_mmap, address, length, protection,
                                       flags, descriptor, (off_t)offset);
    } else {
        errno = EOVERFLOW;
        result = MAP_FAILED;
    }
    if (active) {
        count_mmap(length, flags, result);
        end_recording();
    }
    hook_depth--;
    return result;
}

int munmap(void *address, size_t length) {
    hook_depth++;
    bool active = begin_recording();
    int status = __munmap != NULL ? __munmap(address, length)
                                  : (int)syscall(SYS_munmap, address, length);
    if (active) {
        atomic_fetch_add_explicit(&counters.munmap_api.calls, 1,
                                  memory_order_relaxed);
        add_saturating(&counters.munmap_api.requested_bytes,
                       (unsigned long long)length);
        if (status == 0) {
            size_t actual = 0U;
            if (rounded_mapping_length(length, &actual)) {
                add_saturating(&counters.munmap_api.actual_bytes,
                               (unsigned long long)actual);
                acquire_tracking_lock();
                unsigned long long removed =
                    mmap_untrack_range_locked((uintptr_t)address, actual);
                if (removed > 0U) {
                    remove_live(&counters.mmap_live_bytes, removed);
                }
                if (removed < (unsigned long long)actual) {
                    atomic_fetch_add_explicit(
                        &counters.untracked_munmap_events, 1,
                        memory_order_relaxed);
                }
                release_tracking_lock();
            } else {
                atomic_fetch_add_explicit(
                    &counters.tracking_consistency_events, 1,
                    memory_order_relaxed);
            }
        }
        end_recording();
    }
    hook_depth--;
    return status;
}

static void reset_counter(api_counter *counter) {
    atomic_store_explicit(&counter->calls, 0, memory_order_relaxed);
    atomic_store_explicit(&counter->requested_bytes, 0, memory_order_relaxed);
    atomic_store_explicit(&counter->actual_bytes, 0, memory_order_relaxed);
}

static void reset_all(void) {
    memset(heap_records, 0, sizeof(heap_records));
    memset(mmap_records, 0, sizeof(mmap_records));
    reset_counter(&counters.malloc_api);
    reset_counter(&counters.calloc_api);
    reset_counter(&counters.realloc_api);
    reset_counter(&counters.posix_memalign_api);
    reset_counter(&counters.aligned_alloc_api);
    reset_counter(&counters.memalign_api);
    reset_counter(&counters.valloc_api);
    reset_counter(&counters.pvalloc_api);
    reset_counter(&counters.mmap_api);
    reset_counter(&counters.free_api);
    reset_counter(&counters.munmap_api);
    atomic_store_explicit(&counters.heap_live_bytes, 0, memory_order_relaxed);
    atomic_store_explicit(&counters.mmap_live_bytes, 0, memory_order_relaxed);
    atomic_store_explicit(&counters.combined_peak_live_bytes, 0,
                          memory_order_relaxed);
    atomic_store_explicit(&counters.heap_peak_live_bytes, 0,
                          memory_order_relaxed);
    atomic_store_explicit(&counters.mmap_peak_live_bytes, 0,
                          memory_order_relaxed);
    atomic_store_explicit(&counters.overflow_events, 0, memory_order_relaxed);
    atomic_store_explicit(&counters.accounting_underflow_events, 0,
                          memory_order_relaxed);
    atomic_store_explicit(&counters.hook_reentry_events, 0,
                          memory_order_relaxed);
    atomic_store_explicit(&counters.tracking_table_overflow_events, 0,
                          memory_order_relaxed);
    atomic_store_explicit(&counters.tracking_consistency_events, 0,
                          memory_order_relaxed);
    atomic_store_explicit(&counters.untracked_free_events, 0,
                          memory_order_relaxed);
    atomic_store_explicit(&counters.untracked_realloc_events, 0,
                          memory_order_relaxed);
    atomic_store_explicit(&counters.untracked_munmap_events, 0,
                          memory_order_relaxed);
    atomic_store_explicit(&counters.heap_live_allocations, 0,
                          memory_order_relaxed);
    atomic_store_explicit(&counters.heap_peak_live_allocations, 0,
                          memory_order_relaxed);
    atomic_store_explicit(&counters.mmap_live_mappings, 0,
                          memory_order_relaxed);
    atomic_store_explicit(&counters.mmap_peak_live_mappings, 0,
                          memory_order_relaxed);
    atomic_store_explicit(&snapshot_written, false, memory_order_relaxed);
}

typedef struct {
    char *cursor;
    char *end;
    bool failed;
} json_writer;

static void append_text(json_writer *writer, const char *text) {
    while (*text != '\0') {
        if (writer->cursor >= writer->end) {
            writer->failed = true;
            return;
        }
        *writer->cursor++ = *text++;
    }
}

static void append_u64(json_writer *writer, unsigned long long value) {
    char digits[32];
    size_t length = 0;
    do {
        digits[length++] = (char)('0' + value % 10U);
        value /= 10U;
    } while (value != 0U && length < sizeof(digits));
    while (length > 0) {
        if (writer->cursor >= writer->end) {
            writer->failed = true;
            return;
        }
        *writer->cursor++ = digits[--length];
    }
}

static unsigned long long load_counter(const atomic_ullong *value) {
    return atomic_load_explicit(value, memory_order_relaxed);
}

static void append_api(json_writer *writer, const char *name,
                       const api_counter *counter, bool comma) {
    append_text(writer, "\"");
    append_text(writer, name);
    append_text(writer, "\":{\"calls\":");
    append_u64(writer, load_counter(&counter->calls));
    append_text(writer, ",\"requested_bytes\":");
    append_u64(writer, load_counter(&counter->requested_bytes));
    append_text(writer, ",\"actual_bytes\":");
    append_u64(writer, load_counter(&counter->actual_bytes));
    append_text(writer, comma ? "}," : "}");
}

static void write_snapshot(void) {
    bool already = atomic_exchange_explicit(&snapshot_written, true,
                                            memory_order_relaxed);
    if (already || output_fd < 0) {
        return;
    }
    char buffer[16384];
    json_writer writer = {.cursor = buffer,
                          .end = buffer + sizeof(buffer) - 1,
                          .failed = false};
    append_text(&writer,
                "{\"schema_version\":2,\"status\":\"P0P_ALLOC_OBSERVER_COMPLETE\","
                "\"pid\":");
    append_u64(&writer, (unsigned long long)getpid());
    append_text(&writer, ",\"apis\":{");
    append_api(&writer, "malloc", &counters.malloc_api, true);
    append_api(&writer, "calloc", &counters.calloc_api, true);
    append_api(&writer, "realloc", &counters.realloc_api, true);
    append_api(&writer, "posix_memalign", &counters.posix_memalign_api, true);
    append_api(&writer, "aligned_alloc", &counters.aligned_alloc_api, true);
    append_api(&writer, "memalign", &counters.memalign_api, true);
    append_api(&writer, "valloc", &counters.valloc_api, true);
    append_api(&writer, "pvalloc", &counters.pvalloc_api, true);
    append_api(&writer, "mmap", &counters.mmap_api, true);
    append_api(&writer, "free", &counters.free_api, true);
    append_api(&writer, "munmap", &counters.munmap_api, false);
    append_text(&writer, "},\"heap_live_bytes\":");
    append_u64(&writer, load_counter(&counters.heap_live_bytes));
    append_text(&writer, ",\"mmap_live_bytes\":");
    append_u64(&writer, load_counter(&counters.mmap_live_bytes));
    append_text(&writer, ",\"heap_peak_live_bytes\":");
    append_u64(&writer, load_counter(&counters.heap_peak_live_bytes));
    append_text(&writer, ",\"mmap_peak_live_bytes\":");
    append_u64(&writer, load_counter(&counters.mmap_peak_live_bytes));
    append_text(&writer, ",\"combined_peak_live_bytes\":");
    append_u64(&writer, load_counter(&counters.combined_peak_live_bytes));
    append_text(&writer, ",\"overflow_events\":");
    append_u64(&writer, load_counter(&counters.overflow_events));
    append_text(&writer, ",\"accounting_underflow_events\":");
    append_u64(&writer, load_counter(&counters.accounting_underflow_events));
    append_text(&writer, ",\"hook_reentry_events\":");
    append_u64(&writer, load_counter(&counters.hook_reentry_events));
    append_text(&writer, ",\"tracking_table_overflow_events\":");
    append_u64(
        &writer, load_counter(&counters.tracking_table_overflow_events));
    append_text(&writer, ",\"tracking_consistency_events\":");
    append_u64(&writer,
               load_counter(&counters.tracking_consistency_events));
    append_text(&writer, ",\"untracked_free_events\":");
    append_u64(&writer, load_counter(&counters.untracked_free_events));
    append_text(&writer, ",\"untracked_realloc_events\":");
    append_u64(&writer, load_counter(&counters.untracked_realloc_events));
    append_text(&writer, ",\"untracked_munmap_events\":");
    append_u64(&writer, load_counter(&counters.untracked_munmap_events));
    append_text(&writer, ",\"heap_live_allocations\":");
    append_u64(&writer, load_counter(&counters.heap_live_allocations));
    append_text(&writer, ",\"heap_peak_live_allocations\":");
    append_u64(&writer,
               load_counter(&counters.heap_peak_live_allocations));
    append_text(&writer, ",\"mmap_live_mappings\":");
    append_u64(&writer, load_counter(&counters.mmap_live_mappings));
    append_text(&writer, ",\"mmap_peak_live_mappings\":");
    append_u64(&writer,
               load_counter(&counters.mmap_peak_live_mappings));
    append_text(&writer, ",\"heap_tracking_capacity\":");
    append_u64(&writer, (unsigned long long)HEAP_TRACK_CAPACITY);
    append_text(&writer, ",\"mmap_tracking_capacity\":");
    append_u64(&writer, (unsigned long long)MMAP_TRACK_CAPACITY);
    append_text(&writer, ",\"page_size_bytes\":");
    append_u64(&writer, (unsigned long long)observer_page_size);
    append_text(&writer, ",\"active_recorders_at_snapshot\":");
    append_u64(&writer, load_counter(&active_recorders));
    append_text(&writer, ",\"public_symbols_resolved\":");
    append_text(
        &writer,
        atomic_load_explicit(&public_symbols_resolved, memory_order_relaxed)
            ? "true"
            : "false");
    append_text(&writer, ",\"capture_enabled_at_snapshot\":false}\n");
    if (!writer.failed) {
        size_t length = (size_t)(writer.cursor - buffer);
        ssize_t ignored = (ssize_t)syscall(SYS_write, output_fd, buffer, length);
        (void)ignored;
        (void)syscall(SYS_fsync, output_fd);
    }
}

void p0p_observer_reset_and_start(void) {
    bool already_started = atomic_exchange_explicit(
        &capture_started_once, true, memory_order_acq_rel);
    if (already_started) {
        atomic_fetch_add_explicit(&counters.tracking_consistency_events, 1,
                                  memory_order_relaxed);
        return;
    }
    atomic_store_explicit(&capture_enabled, false, memory_order_seq_cst);
    wait_for_active_recorders();
    acquire_tracking_lock();
    reset_all();
    release_tracking_lock();
    atomic_store_explicit(&capture_enabled, true, memory_order_seq_cst);
}

void p0p_observer_stop_and_write(void) {
    atomic_store_explicit(&capture_enabled, false, memory_order_seq_cst);
    wait_for_active_recorders();
    acquire_tracking_lock();
    write_snapshot();
    release_tracking_lock();
}

static const char *environment_value(const char *key) {
    size_t key_length = strlen(key);
    if (environ == NULL) {
        return NULL;
    }
    for (char **entry = environ; *entry != NULL; ++entry) {
        if (strncmp(*entry, key, key_length) == 0 && (*entry)[key_length] == '=') {
            return *entry + key_length + 1;
        }
    }
    return NULL;
}

__attribute__((constructor)) static void observer_initialize(void) {
    hook_depth++;
    const char *path = environment_value("P0P_ALLOC_OBSERVER_OUTPUT");
    if (path != NULL && path[0] == '/') {
        output_fd = (int)syscall(SYS_openat, AT_FDCWD, path,
                                 O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    }
    long page = sysconf(_SC_PAGESIZE);
    if (page > 0) {
        observer_page_size = (size_t)page;
    }
    real_posix_memalign =
        (int (*)(void **, size_t, size_t))dlsym(RTLD_NEXT, "posix_memalign");
    real_aligned_alloc =
        (void *(*)(size_t, size_t))dlsym(RTLD_NEXT, "aligned_alloc");
    atomic_store_explicit(
        &public_symbols_resolved,
        real_posix_memalign != NULL && real_aligned_alloc != NULL,
        memory_order_relaxed);
    acquire_tracking_lock();
    reset_all();
    release_tracking_lock();
    const char *enabled = environment_value("P0P_ALLOC_OBSERVER_START_ENABLED");
    if (enabled != NULL && strcmp(enabled, "1") == 0) {
        atomic_store_explicit(&capture_started_once, true,
                              memory_order_relaxed);
        atomic_store_explicit(&capture_enabled, true, memory_order_seq_cst);
    }
    hook_depth--;
}

__attribute__((destructor)) static void observer_finalize(void) {
    atomic_store_explicit(&capture_enabled, false, memory_order_seq_cst);
    wait_for_active_recorders();
    acquire_tracking_lock();
    write_snapshot();
    release_tracking_lock();
    if (output_fd >= 0) {
        (void)syscall(SYS_close, output_fd);
        output_fd = -1;
    }
}
