import asyncio

from src.ui.app import LatestSnapshotWriter


class SlowStore:
    """Fake storage whose writes finish only when the test releases them."""

    def __init__(self):
        self.writes = []
        self.started = []
        self.gates = []

    async def write(self, snapshot):
        gate = asyncio.Event()
        self.gates.append(gate)
        self.started.append(snapshot)
        await gate.wait()
        self.writes.append(snapshot)


async def _release_until_done(store, tasks):
    while not all(t.done() for t in tasks):
        for g in store.gates:
            g.set()
        await asyncio.sleep(0)


def test_single_save_writes_snapshot():
    async def run():
        writes = []

        async def write(s):
            writes.append(s)

        await LatestSnapshotWriter(write).save({"v": 1})
        return writes

    assert asyncio.run(run()) == [{"v": 1}]


def test_overlapping_saves_write_newest_last_and_skip_stale():
    async def run():
        store = SlowStore()
        writer = LatestSnapshotWriter(store.write)
        t1 = asyncio.create_task(writer.save({"v": 1}))
        await asyncio.sleep(0)  # t1 holds the lock and is writing v1
        t2 = asyncio.create_task(writer.save({"v": 2}))
        t3 = asyncio.create_task(writer.save({"v": 3}))
        await asyncio.sleep(0)
        assert store.started == [{"v": 1}]  # nothing else writes while v1 is in flight
        await _release_until_done(store, [t1, t2, t3])
        await asyncio.gather(t1, t2, t3)
        return store

    store = asyncio.run(run())
    # v2 was superseded by v3 before it got the lock, so it is never written.
    assert store.writes == [{"v": 1}, {"v": 3}]


def test_writes_never_overlap():
    async def run():
        active = 0
        max_active = 0
        writes = []

        async def write(s):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.001)
            writes.append(s)
            active -= 1

        writer = LatestSnapshotWriter(write)
        await asyncio.gather(*(writer.save({"v": i}) for i in range(20)))
        return writes, max_active

    writes, max_active = asyncio.run(run())
    assert max_active == 1
    assert writes[-1] == {"v": 19}


def test_failed_write_propagates_and_does_not_block_later_saves():
    async def run():
        writes = []
        fail = [True]

        async def write(s):
            if fail[0]:
                fail[0] = False
                raise OSError("disk full")
            writes.append(s)

        writer = LatestSnapshotWriter(write)
        try:
            await writer.save({"v": 1})
        except OSError:
            raised = True
        else:
            raised = False
        await writer.save({"v": 2})
        return raised, writes

    raised, writes = asyncio.run(run())
    assert raised
    # The failed snapshot is not retried; the lock is released, so the next save writes.
    assert writes == [{"v": 2}]
