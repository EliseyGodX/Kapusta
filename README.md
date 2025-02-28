# Kapusta

Kapusta is a simple asynchronous task manager that allows using SQLAlchemy as a broker and result backend (with the possibility of extension). It can work both in the main thread and scale up to multiple processes. It is designed for low-load systems where the number of tasks is small but critical.

> [!WARNING]
>
> This task manager is intended for use cases where the number of tasks is so small that there is no point in creating additional processes and integrating third-party tools (e.g., running Celery, Celery-beat, RabbitMQ for a few deferred tasks per hour).

## Installation

To install Kapusta, clone the repository and install the required dependencies:

```bash
pip install kapusta
```

## Simple Example

```python
import asyncio

from kapusta import AlchemyCRUD, Kapusta

DB_URL = '...'

kapusta = Kapusta(
    crud=AlchemyCRUD(DB_URL)
)


@kapusta.task()
def task():
    print('test')


async def main():
    await kapusta.startup()  # At the start of the application.

    await task.launch()
    # in this example, you need to wait for the task to be completed in the main thread.
    await asyncio.sleep(1)

    await kapusta.shutdown()  # At the end of the application


asyncio.run(main())

```

## Usage

### Running a Kapusta Application

```python
kapusta = Kapusta(
    crud=AlchemyCRUD(DB_URL),
    logger=Logger('kapusta'),
    max_tick_interval=60 * 5,
    default_overdue_time_delta=None,
    default_max_retry_attempts=0,
    default_timeout=60
)

```

When initializing Kapusta, the following arguments are accepted:
- `crud`: An object inherited from `BaseCRUD`. Implements the logic of the broker and result backend.
- `logger`: An object from the standard `logging` module.
- `max_tick_interval`: Defines the maximum time between checks for pending tasks that should be executed soon.
- `default_overdue_time_delta`: Passed by default when registering a task if not overridden. A `timedelta` with the specified time when the task will be considered overdue, `None` if the task should be executed in any case.
- `default_max_retry_attempts`: Passed by default when registering a task if not overridden.
- `default_timeout`: Passed by default when registering a task if not overridden.

```python
await kapusta.startup()
```
When the application starts, an `asyncio.create_task` method `kapusta.listening` is created, which in an infinite loop asynchronously accesses the CRUD service (in the classic implementation, this is `AlchemyCRUD`), takes tasks from there, and launches them. Also, `crud.startup()` is called, necessary for setting up the result backend and broker.

### Defining Tasks

A task is a synchronous or asynchronous function. To register a function as a task, you can use the `kapusta.register_task` method or the `@kapusta.task` decorator (`@kapusta.task` internally calls `kapusta.register_task`).

```python
# Using the decorator

@kapusta.task()
def sample_task(param1, param2):
    return param1 + param2

```

```python
# Using the method

def sample_task(param1, param2):
    return param1 + param2

kapusta.register_task(sample_task)
```

When using both methods, you can pass the following arguments:
```python
"""
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
"""
```

### Launching Tasks

You can launch tasks using the `launch` or `launch_now` methods.

```python
await sample_task.launch(param1=1, param2=2)
await sample_task.launch_now(param1=1, param2=2)
```

At this point, you can also change the task parameters by passing them in `update_params`:
```python
await sample_task.launch(param1=1, param2=2, update_params={'max_retry_attempts': 1})
```

### Retrieving Task Results and Status

You can retrieve the result of a completed task using the `get_task_result` method.

```python
result = await kapusta.get_task_result(task_id)
status = await kapusta.get_task_status(task_id)
```

### CRUD Interface

Kapusta uses a CRUD interface for database operations related to tasks. You can implement your own CRUD interface by extending the `CRUDProtocol` class.

```python
from kapusta import BaseCRUD

class MyCRUD(BaseCRUD):
    # Implement the required methods
    ...
```

## Testing

Kapusta uses `pytest` for testing. You can run the tests using the following command:

```bash
pytest
```
