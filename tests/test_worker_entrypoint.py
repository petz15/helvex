from app import worker_entrypoint


class _FakeAbandonedError(Exception):
    pass


_FakeAbandonedError.__name__ = "AbandonedJobError"
_FakeAbandonedError.__module__ = "rq.registry"


class _NonRqAbandonedError(Exception):
    pass


_NonRqAbandonedError.__name__ = "AbandonedJobError"
_NonRqAbandonedError.__module__ = "myapp.errors"


def test_is_abandoned_job_error_accepts_rq_module_variants():
    assert worker_entrypoint._is_abandoned_job_error(_FakeAbandonedError("x")) is True


def test_is_abandoned_job_error_rejects_non_rq_module():
    assert worker_entrypoint._is_abandoned_job_error(_NonRqAbandonedError("x")) is False
