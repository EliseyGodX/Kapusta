# flake8-in-file-ignores: noqa: WPS226

import asyncio
import pickle
from dataclasses import dataclass
from datetime import datetime, timedelta
from logging import Logger
from typing import Any, Callable, Coroutine, Generic, Mapping, NoReturn, TypeVar

from kapusta.crud import BaseCRUD
from kapusta.models import ResultBackend, TaskExecutionMode, TaskModel, TaskStatus
from kapusta.types import Seconds, Sentinel, TaskId


class KapustaError(Exception): ...
class KapustaSyncTaskTimeoutError(KapustaError): ...
class KapustaValueError(KapustaError): ...
class KapustaExecutionModeError(KapustaError): ...


CRUDType = TypeVar('CRUDType', bound=BaseCRUD)
task_list: dict[str, 'Task'] = {}


@dataclass
class Task:
    """
    Represents a task to be executed in a specific mode with optional delay and retries.

    Attributes:
        func (Callable): The function to execute.
        name (str): The name of the task.
        execution_mode (TaskExecutionMode): The execution mode of the task (sync,
            async_, thread, or process).
        eta (datetime): The estimated time of execution.
        overdue_time (datetime): Time at which a task will be considered overdue
        max_retry_attempts (int): The maximum number of times the task should be
            retried in case of failure.
        timeout (Seconds): The maximum time allowed for task execution.
            Must be non-negative.
        kapusta (Kapusta): The Kapusta instance managing the task.
        kwargs (bytes | None): Serialized keyword arguments for the function.
    """

    func: Callable
    name: str
    execution_mode: TaskExecutionMode
    eta_delta: timedelta | None
    overdue_time_delta: timedelta | None
    max_retry_attempts: int
    timeout: Seconds
    kapusta: 'Kapusta'
    kwargs: bytes = Sentinel

    async def launch(self, update_params: Mapping[str, Any] = Sentinel,
                     **kwargs) -> TaskId:
        """
        Schedules the task for execution, updating its parameters if provided.

        Args:
            update_params (Mapping[str, Any], optional): Parameters to update
            before execution.
            **kwargs: Additional arguments passed to the task function.

        Raises:
            KapustaValueError: If the timeout is negative.
            KapustaSyncTaskTimeout: If the task is synchronous but has a
            non-zero timeout.
            KapustaExecutionModeError: If the execution mode does not match the
            function type (sync/async).

        Returns:
            TaskId: The task identifier.
        """
        if update_params is not Sentinel:
            self.__dict__.update(**update_params)

        if self.timeout < 0:
            raise KapustaValueError('timeout is less than 0.')

        if self.overdue_time_delta and self.overdue_time_delta.total_seconds() < 0:
            raise KapustaValueError('Overdue time delta must be non-negative.')

        if self.execution_mode == TaskExecutionMode.sync and self.timeout != 0:
            raise KapustaSyncTaskTimeoutError(
                'Setting a timeout is not allowed for tasks with sync mode'
                '(timeout must be 0).'
            )

        if (self.execution_mode == TaskExecutionMode.async_
            and not asyncio.iscoroutinefunction(self.func)):
            raise KapustaExecutionModeError(
                'Async execution mode requires a coroutine function.'
            )

        if (self.execution_mode != TaskExecutionMode.async_
            and asyncio.iscoroutinefunction(self.func)):
            raise KapustaExecutionModeError(
                'Sync execution mode does not support coroutine functions.'
            )

        self.kwargs = pickle.dumps(kwargs)
        return await self.kapusta.task_push(self)

    async def launch_now(self, **kwargs) -> TaskId:
        """
        Schedule the task for immediate execution by setting `eta_delta` to None.

        Args:
            **kwargs: Additional keyword arguments to be passed to the task function.

        Returns:
            TaskId: Task ID
        """
        return await self.launch(eta_delta=None, **kwargs)


@dataclass
class Kapusta(Generic[CRUDType]):
    """
    Manages task execution, scheduling, and status tracking.

    Attributes:
        crud (BaseCRUD): The CRUD interface for database operations
            related to tasks.
        logger (Logger): Logger instance for logging.
        max_tick_interval (Seconds): Maximum interval between checks for pending tasks.
        default_overdue_time_delta (timedelta | None): Time after which a pending task
            is considered overdue.
        default_max_retry_attempts (int): Default maximum retry attempts for tasks.
        default_timeout (Seconds): Default execution timeout for tasks.
    """

    crud: CRUDType
    logger: Logger = Logger('kapusta')
    max_tick_interval: Seconds = 60 * 5
    default_overdue_time_delta: timedelta | None = None
    default_max_retry_attempts: int = 0
    default_timeout: Seconds = 60

    async def listening(self) -> NoReturn:  # noqa: WPS217
        """
        Continuously listens for pending tasks.

        - If a task is overdue, its status is updated accordingly.
        - If a task is ready and can be started, it is executed.
        - If no tasks are found, the loop sleeps for `max_tick_interval`.

        Runs indefinitely until manually stopped.
        """
        next_task: TaskModel | None = None
        while True:  # noqa: WPS457
            self.logger.debug('New tick in listening')

            # If the time has come to execute the task that was awaited
            # since the last tick
            if next_task and next_task.eta <= datetime.now():
                if (
                    next_task.overdue_time is not None
                    and next_task.overdue_time <= datetime.now()
                ):
                    self.logger.debug(
                        f'Overdue task {next_task.id} detected in listening'
                    )
                    await self.crud.change_task_status(
                        task_id=next_task.id,
                        change_to=TaskStatus.overdue
                    )

                elif await self.crud.change_task_status(
                    task_id=next_task.id,
                    change_to=TaskStatus.started
                ):
                    self.logger.info(f'Task {next_task.id} started from listening')
                    await self.run_task(next_task)

            # Get the first available task that is scheduled
            # no later than the value of max_tick_interval (including overdue tasks)
            next_task = await self.crud.get_first_pending_task(
                max_eta_delta=self.max_tick_interval
            )
            if not next_task:
                self.logger.debug(
                    'No tasks found, listening will wait max_tick_interval'
                )
                delay = self.max_tick_interval
            else:
                delay = int(
                    (next_task.eta - datetime.now()).total_seconds() + 0.5
                )
                self.logger.debug(
                    f'Got task {next_task.id}, waiting {delay} seconds to execute'
                )
            await asyncio.sleep(delay)

    def create_listening_task(self) -> None:
        """
        Create an asynchronous task for the listening coroutine.

        This method schedules the `listening` coroutine to be run as an asynchronous
        task. It does not block the execution of the program and allows other tasks
        to run concurrently.
        """
        self.listening_task = asyncio.create_task(self.listening())

    def stop_listening_task(self) -> None:
        """
        Cancel the listening task.

        This method stops the asynchronous listening task that continuously checks
        for pending tasks. It cancels the task to prevent further execution.
        """
        self.listening_task.cancel()

    async def crud_shutdown(self) -> None:
        """
        Shutdown the CRUD operations.

        This method performs any necessary cleanup and shutdown operations for the
        CRUD interface. It ensures that all pending database operations are completed
        and resources are released properly.
        """
        await self.crud.shutdown()

    async def startup(self) -> None:
        """
        Asynchronous method to handle startup procedures.

        This method performs the following actions:
        1. Calls the `startup` method of the `crud` attribute.
        2. Creates a listening task by calling `create_listening_task`.
        Returns:
            None
        """
        await self.crud.startup()
        self.create_listening_task()

    async def shutdown(self) -> None:
        """
        Shutdown the Kapusta instance.

        This method stops the listening task and performs CRUD shutdown operations.
        It ensures that the Kapusta instance is properly shut down, releasing all
        resources and completing any pending operations.
        """
        try:
            self.stop_listening_task()
        except NameError:
            pass

        await self.crud_shutdown()

    def register_task(self, func: Callable,
                      execution_mode: TaskExecutionMode = Sentinel,
                      eta_delta: timedelta | None = None,
                      overdue_time_delta: timedelta | None = Sentinel,
                      max_retry_attempts: int = Sentinel,
                      timeout: Seconds = Sentinel, _kapusta: 'Kapusta' = Sentinel
                      ) -> Task:
        """
        Register a new task with the specified parameters.

        Args:
            func (Callable): The function to be executed as a task.
            execution_mode (TaskExecutionMode, optional): The execution mode of the task
                (sync, async_, thread, or process). Defaults to Sentinel.
            eta_delta (timedelta, optional): The time delta for the estimated time of
                execution. If None, the task is executed immediately. Defaults to None.
            overdue_time (timedelta): Time at which a task will be considered overdue.
                Defaults to None.
            max_retry_attempts (int, optional): The maximum number of retry attempts
                for the task. Defaults to Sentinel.
            timeout (Seconds, optional): The maximum time allowed for task execution.
                Defaults to Sentinel.
            _kapusta (Kapusta, optional): The Kapusta instance managing the task.
                Defaults to Sentinel.

        Returns:
            Task: The registered task instance.
        """
        if execution_mode is Sentinel:
            execution_mode = (
                TaskExecutionMode.async_ if asyncio.iscoroutinefunction(func)
                else TaskExecutionMode.sync
            )

        task_name = func.__name__
        task = Task(
            func=func,
            name=task_name,
            execution_mode=execution_mode,
            eta_delta=eta_delta,
            overdue_time_delta=(
                overdue_time_delta if overdue_time_delta is not Sentinel
                else self.default_overdue_time_delta
            ),
            max_retry_attempts=(
                self.default_max_retry_attempts if max_retry_attempts is Sentinel
                else max_retry_attempts
            ),
            timeout=(
                timeout if timeout is not Sentinel else
                (0 if execution_mode == TaskExecutionMode.sync else self.default_timeout)  # noqa: WPS509, E501
            ),
            kapusta=self if _kapusta is Sentinel else _kapusta
        )
        task_list[task_name] = task
        self.logger.info(f'New task {task_name} register')
        return task

    def task(self, execution_mode: TaskExecutionMode = Sentinel,
             eta_delta: timedelta | None = None, overdue_time: timedelta | None = None,
             max_retry_attempts: int = Sentinel,
             timeout: Seconds = Sentinel, _kapusta: 'Kapusta' = Sentinel
             ) -> Callable[..., Task]:
        """
        Decorator to register a function as a task with the specified parameters.

        Args:
            execution_mode (TaskExecutionMode, optional): The execution mode of the task
                (sync, async_, thread, or process). Defaults to Sentinel.
            eta_delta (timedelta, optional): The time delta for the estimated time of
                execution. If None, the task is executed immediately. Defaults to None.
            max_retry_attempts (int, optional): The maximum number of retry attempts
                for the task. Defaults to Sentinel.
            timeout (Seconds, optional): The maximum time allowed for task execution.
                Defaults to Sentinel.
            _kapusta (Kapusta, optional): The Kapusta instance managing the task.
                Defaults to Sentinel.

        Returns:
            Callable[..., Task]: A decorator that registers the function as a task.
        """
        def decorator(func: Callable) -> Task:
            return self.register_task(
                func=func,
                execution_mode=execution_mode,
                eta_delta=eta_delta,
                overdue_time_delta=overdue_time,
                max_retry_attempts=max_retry_attempts,
                timeout=timeout,
                _kapusta=_kapusta
            )

        return decorator

    async def run_task(self, task: TaskModel) -> None:
        """
        Execute a given task using the appropriate execution mode.

        Args:
            task (TaskModel): The sqlalchemy task model.

        The task function is retrieved from `task_list` and executed with
        the provided arguments.
        """
        self.logger.info(f'Task {task.id} started')
        await self._get_executor_task(
            func=task_list[task.name].func,
            kwargs=pickle.loads(task.kwargs),
            execution_mode=task.execution_mode,
            task_id=task.id,
            timeout=task.timeout,
            max_retry_attempts=task.max_retry_attempts
        )

    async def task_push(self, task: Task) -> TaskId:
        """
        Add a task to the execution queue and schedule it for crud insertion.

        Args:
            task (Task): The task instance to be queued.

        Returns:
            TaskId: The integer ID of the task.

        The task is added asynchronously to avoid blocking execution.
        """
        self.logger.info(f'Task {task.name} added to queue')
        return (await self.crud.add_task(
            name=task.name,
            kwargs=task.kwargs,
            execution_mode=task.execution_mode,
            eta=(
                datetime.now() if not task.eta_delta
                else datetime.now() + task.eta_delta
            ),
            overdue_time=(
                None if not task.overdue_time_delta
                else datetime.now() + task.overdue_time_delta
            ),
            max_retry_attempts=task.max_retry_attempts,
            timeout=task.timeout
        )).id

    async def get_task_status(self, task_id: TaskId) -> TaskStatus | None:
        """
        Retrieve the status of a task.

        Args:
            task_id (TaskId): The ID of the task whose status is being retrieved.

        Returns:
            TaskStatus | None: The status of the task if available, otherwise None.
        """
        return await self.crud.get_task_status(task_id)

    async def get_task_result(self, task_id: TaskId) -> ResultBackend | None:
        """
        Retrieve the result of a completed task.

        Args:
            task_id (TaskId): The ID of the task whose result is being retrieved.

        Returns:
            ResultBackend | None: The deserialized result if available, otherwise None.

        The result is fetched from the database and deserialized using pickle.
        """
        result_backend = await self.crud.get_result(task_id)
        if not result_backend:
            return None
        return pickle.loads(result_backend.result)  # type: ignore[reportArgumentType]

    def _get_executor_task(self, func: Callable, kwargs: Mapping[str, Any],
                           execution_mode: TaskExecutionMode, task_id: int,
                           timeout: Seconds, max_retry_attempts: int
                           ) -> Coroutine[Any, Any, None]:
        """
        Create and return a coroutine that executes a task based on the specified mode.

        Args:
            func (Callable): The function to be executed.
            kwargs (Mapping[str, Any]): Keyword arguments to be passed to the function.
            execution_mode (TaskExecutionMode): The execution mode of the task
                (sync, async, thread, or process).
            task_id (int): The unique identifier of the task.
            timeout (Seconds): The maximum time allowed for task execution.
            max_retry_attempts (int): The maximum number of retries if the task fails.

        Returns:
            Coroutine[Any, Any, None]: A coroutine that executes the task according
                to the specified mode.

        The function handles different execution modes:
        - `sync`: Runs the function synchronously.
        - `async_`: Runs the function as an asynchronous coroutine.
        - `thread`: Runs the function in a separate thread.
        - `process`: Runs the function in a separate process.

        If the task completes successfully, the result is stored in the database.
        If all retry attempts fail, the task is marked as an error.
        """
        async def executor(task: Callable[..., Coroutine]) -> None:
            error = False
            self.logger.info(f'Task {task_id} started execution')
            for i in range(max_retry_attempts + 1):
                try:
                    task_result = await task()
                    self.logger.info(
                        f'Task {task_id} executed {i + 1} times'
                    )
                    error = False

                except asyncio.TimeoutError:
                    self.logger.warning(f'Task {task_id} timed out')
                    await self.crud.change_task_status(
                        task_id=task_id,
                        change_to=TaskStatus.timeout
                    )

                except Exception as e:
                    self.logger.warning(f'Exception {e} in task {task_id}')
                    error = True

            if not error:
                await self.crud.add_result(
                    task_id,
                    task_result=pickle.dumps(task_result)  # type: ignore
                )
                await self.crud.change_task_status(
                    task_id=task_id,
                    change_to=TaskStatus.success
                )
            else:
                await self.crud.change_task_status(
                    task_id=task_id,
                    change_to=TaskStatus.error
                )

        if execution_mode == TaskExecutionMode.sync:
            async def prepare_task() -> Any:
                return func(**kwargs)

        elif execution_mode == TaskExecutionMode.async_:
            async def prepare_task() -> Any:
                task = asyncio.create_task(func(**kwargs))
                return await asyncio.wait_for(task, timeout if timeout != 0 else None)

        elif execution_mode == TaskExecutionMode.thread:
            async def prepare_task() -> Any:
                task = asyncio.to_thread(func, **kwargs)
                return await asyncio.wait_for(task, timeout if timeout != 0 else None)

        elif execution_mode == TaskExecutionMode.process:
            async def prepare_task() -> Any:
                task = asyncio.get_running_loop().run_in_executor(
                    None, lambda: func(**kwargs)
                )
                return await asyncio.wait_for(task, timeout if timeout != 0 else None)

        else:
            raise KapustaExecutionModeError(
                f'{execution_mode=} is not implemented'
                'in executor'
            )

        return executor(prepare_task)
