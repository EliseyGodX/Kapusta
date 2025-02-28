from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from kapusta.types import Seconds


class TaskExecutionMode(Enum):
    """
    This Enum class provides task execution modes.

    Attributes:
        sync (str): Executes in the same thread where kapusta is running synchronously
            and does not support the timeout parameter.
        async_ (str): Executes asynchronously.
        thread (str): Executes in a new thread.
        process (str): Executes in a new process.
    Note:
        If sync, thread, or process is selected, the task must be synchronous
        (without the async keyword).
    """

    sync = 'sync'
    async_ = 'async_'
    thread = 'thread'
    process = 'process'


class TaskStatus(Enum):
    """
    TaskStatus is an enumeration that represents the various states a task can be in.

    Attributes:
        pending (str): The task is pending and has not started yet.
        started (str): The task has started and is currently in progress.
        success (str): The task has completed successfully.
        error (str): The task has encountered an error.
        timeout (str): The task has timed out.
        overdue (str): Task is overdue and not completed.
    """

    pending = 'pending'
    started = 'started'
    success = 'success'
    error = 'error'
    timeout = 'timeout'
    overdue = 'overdue'


class Base(DeclarativeBase):
    pass


class TaskModel(Base):
    __tablename__ = 'kapusta_tasks'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    kwargs: Mapped[bytes] = mapped_column(LargeBinary, nullable=True, default=None)
    execution_mode: Mapped[TaskExecutionMode] = mapped_column(
        SAEnum(TaskExecutionMode, name='execution_mode'), nullable=False
    )
    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(TaskStatus, name='status'), nullable=False, default=TaskStatus.pending
    )
    eta: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now()
    )
    overdue_time: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=None
    )
    max_retry_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    timeout: Mapped[Seconds] = mapped_column(Integer, nullable=False, default=10)

    result_backends: Mapped[list['ResultBackend']] = relationship(
        'ResultBackend', back_populates='task'
    )


class ResultBackend(Base):
    __tablename__ = 'kapusta_result_backends'

    id: Mapped[int] = mapped_column(
        Integer, ForeignKey('kapusta_tasks.id', ondelete='CASCADE'), primary_key=True
    )
    result: Mapped[bytes | None] = mapped_column(  # noqa: WPS110
        LargeBinary, nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now()
    )

    task = relationship('TaskModel', back_populates='result_backends')
