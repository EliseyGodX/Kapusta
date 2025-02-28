import asyncio
from contextlib import AbstractContextManager
from datetime import datetime, timedelta
from time import sleep
from typing import Callable

import pytest

from kapusta import (Kapusta, KapustaExecutionModeError,
                     KapustaSyncTaskTimeoutError, KapustaValueError,
                     TaskExecutionMode, TaskStatus)
from kapusta.types import Seconds

test_kwargs = {'test': True}


def sync_task(**kwargs) -> dict:
    return kwargs


def long_sync_task(timeout: Seconds, **kwargs) -> dict:
    sleep(timeout * timeout)
    return kwargs


async def async_task(**kwargs) -> dict:
    return kwargs


async def long_async_task(timeout: Seconds, **kwargs) -> dict:
    await asyncio.sleep(timeout * timeout)
    return kwargs


@pytest.mark.parametrize(
    argnames='func, execution_mode, eta_delta, timeout, kwargs, completion_status',
    argvalues=[
        [sync_task, TaskExecutionMode.sync, None, 0, test_kwargs, TaskStatus.success],
        [sync_task, TaskExecutionMode.thread, None, 0, test_kwargs, TaskStatus.success],
        [sync_task, TaskExecutionMode.process, None, 0, test_kwargs, TaskStatus.success],  # noqa: E501
        [sync_task, TaskExecutionMode.sync, timedelta(seconds=2), 0, test_kwargs, TaskStatus.success],  # noqa: E501
        [sync_task, TaskExecutionMode.thread, timedelta(seconds=2), 0, test_kwargs, TaskStatus.success],  # noqa: E501
        [sync_task, TaskExecutionMode.process, timedelta(seconds=2), 0, test_kwargs, TaskStatus.success],  # noqa: E501
        [sync_task, TaskExecutionMode.thread, None, 2, test_kwargs, TaskStatus.success],
        [sync_task, TaskExecutionMode.process, None, 2, test_kwargs, TaskStatus.success],  # noqa: E501
        [long_sync_task, TaskExecutionMode.thread, None, 2, {'timeout': 2, **test_kwargs}, TaskStatus.timeout],  # noqa: E501
        [long_sync_task, TaskExecutionMode.process, None, 2, {'timeout': 2, **test_kwargs}, TaskStatus.timeout],  # noqa: E501
        [async_task, TaskExecutionMode.async_, None, 0, test_kwargs, TaskStatus.success],  # noqa: E501
        [async_task, TaskExecutionMode.async_, timedelta(seconds=2), 0, test_kwargs, TaskStatus.success],  # noqa: E501
        [async_task, TaskExecutionMode.async_, None, 2, test_kwargs, TaskStatus.success],  # noqa: E501
        [long_async_task, TaskExecutionMode.async_, None, 2, {'timeout': 2, **test_kwargs}, TaskStatus.timeout]  # noqa: E501
    ]
)
@pytest.mark.asyncio
async def test_task(func: Callable, execution_mode: TaskExecutionMode,
                    eta_delta: timedelta | None, timeout: Seconds, kwargs: dict,
                    completion_status: TaskStatus, kapusta: Kapusta
                    ) -> None:
    await kapusta.startup()

    try:
        task = kapusta.register_task(
            func=func,
            execution_mode=execution_mode,
            eta_delta=eta_delta,
            max_retry_attempts=0,
            timeout=timeout
        )

        task_id = (await task.launch(**kwargs))
        task_start_time = datetime.now()

        if completion_status == TaskStatus.success:
            while True:
                await asyncio.sleep(eta_delta.total_seconds() if eta_delta else 0.5)
                task_result = await kapusta.get_task_result(task_id)
                if not task_result:
                    continue
                assert task_result == kwargs
                break

        else:
            while True:
                await asyncio.sleep(eta_delta.total_seconds() if eta_delta else 0.5)
                task_status = await kapusta.get_task_status(task_id)
                if (not task_status
                    or task_status == TaskStatus.started
                    or task_status == TaskStatus.pending):
                    continue
                assert task_status == completion_status
                break

        if eta_delta is not None:
            assert datetime.now() - task_start_time >= eta_delta

    finally:
        await kapusta.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    argnames='func, execution_mode, eta_delta, timeout, kwargs, expectation',
    argvalues=[
        [async_task, TaskExecutionMode.async_, None, -2, test_kwargs, pytest.raises(KapustaValueError)],  # noqa: E501
        [sync_task, TaskExecutionMode.sync, None, 2, test_kwargs, pytest.raises(KapustaSyncTaskTimeoutError)],  # noqa: E501
        [sync_task, TaskExecutionMode.async_, None, 0, test_kwargs, pytest.raises(KapustaExecutionModeError)],  # noqa: E501
        [async_task, TaskExecutionMode.sync, None, 0, test_kwargs, pytest.raises(KapustaExecutionModeError)],  # noqa: E501
        [async_task, TaskExecutionMode.thread, None, 0, test_kwargs, pytest.raises(KapustaExecutionModeError)],  # noqa: E501
        [async_task, TaskExecutionMode.process, None, 0, test_kwargs, pytest.raises(KapustaExecutionModeError)],  # noqa: E501
    ]
)
async def test_invalid_task(func: Callable, execution_mode: TaskExecutionMode,
                            eta_delta: timedelta | None, timeout: Seconds, kwargs: dict,
                            expectation: AbstractContextManager, kapusta: Kapusta
                            ) -> None:
    with expectation:
        task = kapusta.register_task(
            func=func,
            execution_mode=execution_mode,
            eta_delta=eta_delta,
            max_retry_attempts=0,
            timeout=timeout
        )
        await task.launch(**kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    argnames='func',
    argvalues=[sync_task]
)
async def test_task_overdue_marking(func: Callable, kapusta: Kapusta) -> None:
    # we do not call kapusta.listening() so that the task is not completed
    # and becomes overdue.
    await kapusta.crud.startup()

    try:
        task = kapusta.register_task(
            func, overdue_time_delta=timedelta(seconds=1)
        )
        task_id = await task.launch()
        sleep(2)
        kapusta.create_listening_task()

        while True:
            await asyncio.sleep(0.5)
            task_status = await kapusta.get_task_status(task_id)
            if not task_status:
                continue
            assert task_status == TaskStatus.overdue
            break

    finally:
        await kapusta.shutdown()
