# Future Enhancements

This document tracks potential improvements and enhancements for the WWII data extraction pipeline.

## Concurrency & Distributed Processing

### Current Limitations

**Single-Server Design**
- Phase2 extraction designed for single server execution
- Concurrent execution from multiple servers causes data corruption
- File locking (fcntl) only works on same server

**Race Conditions**
```python
# Current code has read-modify-write races:
if person_file.exists():
    existing = json.loads(person_file.read_text())  # No lock
    existing["events"].extend(new_events)
    validate_and_write_json(person_file, existing, use_lock=False)  # No lock
```

**Issues with Concurrent Execution:**
1. **Data Loss** - Last write wins, earlier changes lost
2. **File Corruption** - Simultaneous writes create invalid JSON
3. **Inconsistent State** - Partial data from multiple servers mixed
4. **Validation Corruption** - History and reports show incorrect data

### AWS EFS Considerations

**What Works:**
- ✅ POSIX file locking (fcntl) works across EC2 instances
- ✅ Atomic file operations
- ✅ Close-to-open consistency

**What Doesn't Work:**
- ❌ Read-modify-write still racy (no lock during read)
- ❌ Lock contention serializes writes (no parallelism)
- ❌ Higher latency (~1-3ms vs <0.1ms local)
- ❌ Most extraction code uses `use_lock=False`

**Files with Race Conditions:**
- `src/extraction/people.py` - No locking on read
- `src/extraction/equipment.py` - No locking on read
- `src/extraction/people_groups.py` - No locking on read
- `src/extraction/maps.py` - No locking on read
- `src/extraction/casualties.py` - No locking on read

### Enhancement Options

#### Option A: Fix Distributed Locking (Medium Effort)

**Changes Required:**
1. Add locking to all read-modify-write operations
2. Change `use_lock=False` to `use_lock=True` throughout codebase
3. Implement lock acquisition during read phase

**Implementation:**
```python
def _save_person_file(person_data, filepath):
    with file_lock(filepath):  # Lock during entire operation
        if filepath.exists():
            existing = json.loads(filepath.read_text())
            existing["events"].extend(person_data["events"])
        else:
            existing = person_data
        validate_and_write_json(filepath, existing, PEOPLE_SCHEMA, use_lock=False)
```

**Pros:**
- Works on EFS across servers
- Prevents data corruption
- Minimal code changes

**Cons:**
- Serializes writes to same file (no parallelism)
- Higher latency on EFS
- Lock contention under high concurrency

**Estimated Effort:** 2-4 hours
- Update 5 extraction modules
- Add context manager for read-modify-write
- Test on EFS

#### Option B: Partition by Chapter (Low Effort)

**Implementation:**
```bash
# Server 1
python phase2_extract.py --chapters 1-50

# Server 2
python phase2_extract.py --chapters 51-100
```

**Pros:**
- No code changes needed
- True parallelism
- No locking required
- Works on any filesystem

**Cons:**
- Manual coordination required
- Uneven workload distribution
- Doesn't help with single chapter processing

**Estimated Effort:** 30 minutes
- Add `--chapters` CLI argument
- Filter chapters in main loop

#### Option C: Distributed Locking with Redis (High Effort)

**Implementation:**
```python
import redis
from redis.lock import Lock

redis_client = redis.Redis(host='redis-host')

def _save_person_file(person_data, filepath):
    lock_key = f"lock:{filepath}"
    with Lock(redis_client, lock_key, timeout=30):
        if filepath.exists():
            existing = json.loads(filepath.read_text())
            existing["events"].extend(person_data["events"])
        else:
            existing = person_data
        validate_and_write_json(filepath, existing, PEOPLE_SCHEMA, use_lock=False)
```

**Pros:**
- Works across any servers
- Fine-grained locking
- Timeout handling
- Lock monitoring

**Cons:**
- Requires Redis infrastructure
- Additional dependency
- More complex deployment
- Network latency for locks

**Estimated Effort:** 1-2 days
- Set up Redis
- Implement distributed lock manager
- Update all extraction modules
- Add retry logic
- Test failover scenarios

#### Option D: Queue-Based Processing (High Effort)

**Architecture:**
```
Coordinator → SQS Queue → Worker 1
                       → Worker 2
                       → Worker N

Workers write to separate directories
Post-processor merges results
```

**Implementation:**
```python
# Coordinator
for chapter in chapters:
    for event in chapter.events:
        queue.send_message({"chapter": chapter, "event": event})

# Worker
while True:
    message = queue.receive_message()
    process_event(message)
    write_to_worker_directory(data, worker_id)

# Post-processor
merge_worker_results()
```

**Pros:**
- Highly scalable
- Auto-scaling workers
- Fault tolerant
- No file locking needed

**Cons:**
- Major architecture change
- Requires AWS SQS/RabbitMQ
- Complex deployment
- Merge logic needed

**Estimated Effort:** 1-2 weeks
- Design queue architecture
- Implement coordinator
- Update workers
- Build merge logic
- Test at scale

#### Option E: Append-Only + Merge (Medium Effort)

**Implementation:**
```python
# Each server writes to own directory
output_dir = f"output_{server_id}"

# After all servers complete
python scripts/merge_server_outputs.py output_1 output_2 output_3
```

**Merge Logic:**
```python
def merge_person_files(files):
    merged = {}
    for file in files:
        data = json.loads(file.read_text())
        if data["PersonID"] not in merged:
            merged[data["PersonID"]] = data
        else:
            # Merge events, deduplicate
            merged[data["PersonID"]]["events"].extend(data["events"])
    return merged
```

**Pros:**
- No locking needed
- True parallelism
- Simple to implement
- Works on any filesystem

**Cons:**
- Requires merge step
- Duplicate detection needed
- More disk space during processing
- Post-processing time

**Estimated Effort:** 4-8 hours
- Update extraction to use server-specific dirs
- Implement merge script
- Add deduplication logic
- Test merge correctness

### Recommendation

**For immediate use:**
- **Option B (Partition by Chapter)** - Quick, simple, effective

**For production scale:**
- **Option E (Append-Only + Merge)** - Good balance of effort vs benefit

**For enterprise scale:**
- **Option D (Queue-Based)** - Most scalable, requires infrastructure investment

### Related Enhancements

**Performance Optimizations:**
- Parallel validation of directories
- Incremental validation (only changed files)
- Validation result caching
- Progress bars for long operations

**Monitoring:**
- Lock contention metrics
- Processing throughput
- Error rates by server
- Merge conflict detection

**Testing:**
- Concurrent execution tests
- Race condition detection
- EFS performance benchmarks
- Merge correctness validation

## Implementation Priority

1. **High Priority:** Option B (Partition by Chapter) - Quick win
2. **Medium Priority:** Option E (Append-Only + Merge) - Production ready
3. **Low Priority:** Option A (Fix Locking) - If EFS required
4. **Future:** Option D (Queue-Based) - Enterprise scale

## Notes

- Current validation system (JSON schema, custom validators) works with all options
- Pre-commit hooks and GitHub Actions unaffected
- Validation reports and dashboard compatible with all approaches
- Type stubs and schema evolution independent of concurrency model

## References

- AWS EFS Documentation: https://docs.aws.amazon.com/efs/
- POSIX File Locking: `man fcntl`
- Redis Distributed Locks: https://redis.io/docs/manual/patterns/distributed-locks/
- AWS SQS: https://aws.amazon.com/sqs/
