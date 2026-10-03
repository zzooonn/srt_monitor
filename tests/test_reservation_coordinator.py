import asyncio

from backend.reservation_coordinator import AttemptOutcome, ReservationCoordinator


def test_initial_state_blocks_until_started():
    c = ReservationCoordinator()
    assert c.blocked_reason == "stopped"
    assert c.count == 0


def test_begin_session_unblocks_and_allows_attempt():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=1)
    assert c.blocked_reason is None

    attempt = asyncio.run(c.try_begin_attempt("srt"))
    assert attempt is not None
    assert attempt.service == "srt"


def test_reserved_outcome_increments_count_and_reaches_goal():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=1)

    async def flow():
        attempt = await c.try_begin_attempt("srt")
        await c.finish_attempt(attempt, AttemptOutcome.RESERVED)

    asyncio.run(flow())
    assert c.count == 1
    assert c.goal_reached
    assert c.blocked_reason == "goal_reached"


def test_not_reserved_outcome_allows_next_attempt():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=1)

    async def flow():
        attempt = await c.try_begin_attempt("srt")
        await c.finish_attempt(attempt, AttemptOutcome.NOT_RESERVED)
        return await c.try_begin_attempt("ktx")

    second = asyncio.run(flow())
    assert second is not None
    assert c.count == 0


def test_only_one_side_can_hold_in_flight_attempt():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=5)

    async def flow():
        first = await c.try_begin_attempt("srt")
        second = await c.try_begin_attempt("ktx")
        return first, second

    first, second = asyncio.run(flow())
    assert first is not None
    assert second is None


def test_concurrent_try_begin_attempt_only_one_wins():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=5)

    async def race():
        return await asyncio.gather(
            c.try_begin_attempt("srt"),
            c.try_begin_attempt("ktx"),
        )

    results = asyncio.run(race())
    non_none = [r for r in results if r is not None]
    assert len(non_none) == 1


def test_uncertain_outcome_blocks_until_resolved():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=5)

    async def flow():
        attempt = await c.try_begin_attempt("srt")
        await c.finish_attempt(attempt, AttemptOutcome.UNCERTAIN)

    asyncio.run(flow())
    assert c.blocked_reason == "uncertain"
    assert asyncio.run(c.try_begin_attempt("ktx")) is None

    # restarting the session must NOT clear an unresolved uncertain attempt
    c.begin_or_resume_session(max_reservations=5)
    assert c.blocked_reason == "uncertain"
    assert asyncio.run(c.try_begin_attempt("ktx")) is None

    c.resolve_uncertain(found=False)
    assert c.blocked_reason is None
    assert c.count == 0


def test_resolve_uncertain_found_true_counts_toward_goal():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=1)

    async def flow():
        attempt = await c.try_begin_attempt("srt")
        await c.finish_attempt(attempt, AttemptOutcome.UNCERTAIN)

    asyncio.run(flow())
    c.resolve_uncertain(found=True)
    assert c.count == 1
    assert c.blocked_reason == "goal_reached"


def test_begin_or_resume_session_preserves_count_while_attempt_in_flight():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=5)

    async def start_attempt():
        return await c.try_begin_attempt("srt")

    attempt = asyncio.run(start_attempt())
    assert attempt is not None

    # a restart while an attempt is still in flight must not reset the count
    c.begin_or_resume_session(max_reservations=5)
    assert c.count == 0  # nothing reserved yet, but critically no exception and no corruption

    asyncio.run(c.finish_attempt(attempt, AttemptOutcome.RESERVED))
    assert c.count == 1

    # a *later* restart, now that nothing is in flight/uncertain, is a fresh session
    c.begin_or_resume_session(max_reservations=5)
    assert c.count == 0


def test_mark_stopped_preserves_count_and_uncertain():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=5)

    async def flow():
        attempt = await c.try_begin_attempt("srt")
        await c.finish_attempt(attempt, AttemptOutcome.RESERVED)

    asyncio.run(flow())
    c.mark_stopped()
    assert c.count == 1
    assert c.blocked_reason == "stopped"

    # a naive "start" must not silently wipe the count even though stopped
    assert c.public_state()["count"] == 1


def test_duplicate_finish_attempt_is_ignored():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=5)

    async def flow():
        attempt = await c.try_begin_attempt("srt")
        await c.finish_attempt(attempt, AttemptOutcome.RESERVED)
        await c.finish_attempt(attempt, AttemptOutcome.RESERVED)  # duplicate completion

    asyncio.run(flow())
    assert c.count == 1


def test_stale_attempt_finish_is_ignored():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=5)

    async def flow():
        first = await c.try_begin_attempt("srt")
        await c.finish_attempt(first, AttemptOutcome.NOT_RESERVED)
        second = await c.try_begin_attempt("ktx")
        # a late callback for the already-finished first attempt must not
        # touch the state of the (unrelated) second attempt
        await c.finish_attempt(first, AttemptOutcome.RESERVED)
        return second

    second = asyncio.run(flow())
    assert c.count == 0
    assert second is not None


def test_public_state_shape():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=3)
    state = c.public_state()
    assert state == {
        "count": 0,
        "max": 3,
        "blocked": False,
        "reason": None,
        "uncertain_service": None,
    }
