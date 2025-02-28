from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from logging import Logger
from typing import Sequence

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from kapusta.models import (Base, ResultBackend, TaskExecutionMode, TaskModel,
                            TaskStatus)
from kapusta.types import Seconds, Sentinel


class BaseCRUD(ABC):
    """A protocol that defines the CRUD operations for Kapusta."""

    @abstractmethod
    async def startup(self, logger: Logger = Sentinel) -> None:
        """
        Initialize the CRUD operations.

        Args:
            logger (Logger, optional): Logger instance for logging.
                Defaults to Sentinel.
        """
        ...

    @abstractmethod
    async def add_task(self, name: str, kwargs: bytes,
                       execution_mode: TaskExecutionMode, eta: datetime,
                       overdue_time: datetime | None, max_retry_attempts: int,
                       timeout: Seconds) -> TaskModel:
        """
        Add a new task to the task management system.

        Args:
            name (str): The name of the task.
            kwargs (bytes): kwargs for the task.
            execution_mode (TaskExecutionMode): The execution mode of the task.
            eta (datetime): Estimated time of arrival for the task.
            overdue_time (datetime | None): Time at which a task will be
                considered overdue
            max_retry_attempts (int): Maximum number of retry attempts.
            timeout (Seconds): Timeout duration for the task.
        Returns:
            TaskModel: The created task model.
        """
        ...

    @abstractmethod
    async def get_task(self, task_id: int) -> TaskModel | None:
        """
        Retrieve a task by its ID.

        Args:
            task_id (int): The ID of the task.
        Returns:
            TaskModel | None: The task model if found, otherwise None.
        """
        ...

    @abstractmethod
    async def get_task_status(self, task_id: int) -> TaskStatus | None:
        """
        Retrieve the status of a task by its ID.

        Args:
            task_id (int): The ID of the task.
        Returns:
            TaskStatus | None: The status of the task if found, otherwise None.
        """
        ...

    @abstractmethod
    async def get_first_pending_task(self, max_eta_delta: Seconds) -> TaskModel | None:
        """
        Retrieve the first pending task within the maximum ETA delta.

        Args:
            max_eta_delta (Seconds): The maximum ETA delta.
        Returns:
            TaskModel | None: The first pending task model if found, otherwise None.
        """
        ...

    @abstractmethod
    async def change_task_status(self, task_id: int, change_to: TaskStatus) -> bool:
        """
        Change the status of a task.

        Args:
            task_id (int): The ID of the task.
            change_to (TaskStatus): The new status to change to.
        Returns:
            bool: True if the status was changed successfully, otherwise False.
        """
        ...

    @abstractmethod
    async def add_result(self, id: int, task_result: bytes) -> ResultBackend:
        """
        Add a result for a task.

        Args:
            id (int): The ID of the task.
            task_result (bytes): The result of the task.
        Returns:
            ResultBackend: The result backend instance.
        """
        ...

    @abstractmethod
    async def get_result(self, id: int) -> ResultBackend | None:
        """
        Retrieve the result of a task by its ID.

        Args:
            id (int): The ID of the task.
        Returns:
            ResultBackend | None: The result backend if found, otherwise None.
        """

    @abstractmethod
    async def shutdown(self) -> None:
        """Shutdown the CRUD operations."""


class AlchemyCRUD(BaseCRUD):

    def __init__(self, db_url: str, **create_engine_kwargs) -> None:
        self.engine = create_async_engine(db_url, **create_engine_kwargs)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def startup(self, logger: Logger = Sentinel) -> None:
        self.logger = Logger('kapusta.alchemy_crud') if logger is Sentinel else logger
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def add_task(self, name: str, kwargs: bytes,
                       execution_mode: TaskExecutionMode, eta: datetime,
                       overdue_time: datetime | None, max_retry_attempts: int,
                       timeout: Seconds) -> TaskModel:
        self.logger.info(f"Adding task: {name}")
        async with self.sessionmaker() as session:
            new_task = TaskModel(
                name=name,
                kwargs=kwargs,
                execution_mode=execution_mode,
                eta=eta,
                overdue_time=overdue_time,
                max_retry_attempts=max_retry_attempts,
                timeout=timeout,
                status=TaskStatus.pending
            )
            session.add(new_task)
            await session.commit()
            return new_task

    async def get_task(self, task_id: int) -> TaskModel | None:
        self.logger.info(f"Getting task with id: {task_id}")
        async with self.sessionmaker() as session:
            query_result = await session.execute(
                select(TaskModel)
                .where(TaskModel.id == task_id)
            )
            return query_result.scalars().first()

    async def get_task_status(self, task_id: int) -> TaskStatus | None:
        self.logger.info(f"Getting task status for task id: {task_id}")
        async with self.sessionmaker() as session:
            query_result = await session.execute(
                select(TaskModel.status)
                .where(TaskModel.id == task_id)
            )
            return query_result.scalars().first()

    async def get_first_pending_task(self, max_eta_delta: Seconds) -> TaskModel | None:
        self.logger.info('Getting first pending task')
        async with self.sessionmaker() as session:
            query_result = await session.execute(
                select(TaskModel)
                .where(TaskModel.eta <= datetime.now()
                       + timedelta(seconds=max_eta_delta))
                .where(TaskModel.status == TaskStatus.pending)
                .order_by(TaskModel.eta)
            )
            return query_result.scalars().first()

    async def change_task_status(self, task_id: int, change_to: TaskStatus) -> bool:
        self.logger.info(f"Changing task status to {change_to} for task id: {task_id}")
        async with self.sessionmaker() as session:
            query_result = await session.execute(
                select(TaskModel)
                .where(TaskModel.id == task_id)
                .with_for_update()
            )
            task = query_result.scalars().first()
            if not task or task.status == change_to:
                return False
            else:
                task.status = change_to
                await session.commit()
                return True

    async def add_result(self, id: int, task_result: bytes) -> ResultBackend:
        self.logger.info(f"Adding result for task id: {id}")
        async with self.sessionmaker() as session:
            new_result = ResultBackend(
                id=id,
                result=task_result
            )
            session.add(new_result)
            await session.commit()
            return new_result

    async def get_result(self, id: int) -> ResultBackend | None:
        self.logger.info(f"Getting result for task id: {id}")
        async with self.sessionmaker() as session:
            query_result = await session.execute(
                select(ResultBackend)
                .where(ResultBackend.id == id)
            )
            return query_result.scalars().first()

    async def shutdown(self) -> None:
        await self.engine.dispose()

    async def del_old_tasks(self, delta: timedelta, only_successful: bool = False
                            ) -> Sequence[TaskModel]:
        """
        Deletes old tasks from the database based on the specified time delta.

        Args:
            delta (timedelta): The time delta to determine which tasks are
                considered old.
            only_successful (bool, optional): If True, only delete tasks that have
                a status of success. Defaults to False.
        Returns:
            Sequence[TaskModel]: A sequence of TaskModel instances that were deleted.
        Logs:
            Logs the deletion process with the status of only_successful.
        """
        self.logger.info(f'Deleting old tasks ({only_successful=})')
        async with self.sessionmaker() as session:
            cutoff_time = datetime.now() - delta
            stmt = (
                select(TaskModel)
                .join(ResultBackend, TaskModel.id == ResultBackend.id)
                .where(ResultBackend.created_at <= cutoff_time)
            )
            if only_successful:
                stmt = stmt.where(TaskModel.status == TaskStatus.success)
            old_tasks = (await session.execute(stmt)).scalars().all()

            for task in old_tasks:
                await session.delete(task)
            await session.commit()
            return old_tasks
