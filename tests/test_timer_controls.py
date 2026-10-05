"""Exercise the timer WebSocket handler without a Home Assistant install."""

import ast
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).parents[1] / 'custom_components/voice_satellite/__init__.py'


def load_handler():
    module = ast.parse(SOURCE.read_text())
    handler = next(node for node in module.body
                   if isinstance(node, ast.AsyncFunctionDef) and node.name == 'ws_pause_timer')
    handler.decorator_list = []
    handler.body = [node for node in handler.body if not isinstance(node, ast.ImportFrom)]
    namespace = {
        'TIMER_DATA': 'timers',
        '_LOGGER': logging.getLogger('test_timer_controls'),
        '_find_entity': lambda hass, entity_id: hass.entities.get(entity_id),
    }
    exec(compile(ast.Module(body=[handler], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace['ws_pause_timer']


class TimerControlsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.handler = load_handler()
        self.calls = []
        self.responses = []
        self.manager = SimpleNamespace(
            timers={'pasta': SimpleNamespace(id='pasta', device_id='kitchen')},
            pause_timer=lambda timer_id: self.calls.append(('pause', timer_id)),
            unpause_timer=lambda timer_id: self.calls.append(('resume', timer_id)),
        )
        self.hass = SimpleNamespace(data={'timers': self.manager}, entities={
            'assist_satellite.kitchen': SimpleNamespace(device_entry=SimpleNamespace(id='kitchen')),
            'assist_satellite.bedroom': SimpleNamespace(device_entry=SimpleNamespace(id='bedroom')),
        })
        self.connection = SimpleNamespace(
            send_error=lambda *args: self.responses.append(('error', *args)),
            send_result=lambda *args: self.responses.append(('ok', *args)),
        )
        self.message = {'id': 1, 'entity_id': 'assist_satellite.kitchen', 'timer_id': 'pasta', 'paused': True}

    async def test_pause_and_resume(self):
        await self.handler(self.hass, self.connection, self.message)
        await self.handler(self.hass, self.connection, {**self.message, 'paused': False})
        self.assertEqual(self.calls, [('pause', 'pasta'), ('resume', 'pasta')])
        self.assertTrue(all(reply[0] == 'ok' for reply in self.responses))

    async def test_another_satellites_timer_is_rejected(self):
        await self.handler(self.hass, self.connection, {
            **self.message, 'entity_id': 'assist_satellite.bedroom',
        })
        self.assertEqual(self.calls, [])
        self.assertEqual(self.responses[0][2], 'not_found')

    async def test_missing_timer_and_unavailable_manager(self):
        await self.handler(self.hass, self.connection, {**self.message, 'timer_id': 'gone'})
        self.assertEqual(self.responses[-1][2], 'not_found')
        self.hass.data.clear()
        await self.handler(self.hass, self.connection, self.message)
        self.assertEqual(self.responses[-1][2], 'not_ready')


if __name__ == '__main__':
    unittest.main()
