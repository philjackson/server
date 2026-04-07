"""Tests for SyncGroupPlayer."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from music_assistant_models.enums import PlaybackState

from music_assistant.constants import CONF_DYNAMIC_GROUP_MEMBERS, CONF_GROUP_MEMBERS
from music_assistant.providers.sync_group.player import SyncGroupPlayer
from tests.common import MockPlayer, MockProvider, create_mock_config


def _create_mass() -> tuple[MagicMock, dict[str, tuple[float, object, tuple[object, ...], dict]]]:
    """Create a mocked MusicAssistant instance with inspectable timers."""
    timers: dict[str, tuple[float, object, tuple[object, ...], dict]] = {}
    mass = MagicMock()
    mass.closing = False
    mass.loop = None
    mass.config = MagicMock()
    mass.config.set_raw_player_config_value = MagicMock()

    def get_base_player_config(player_id: str, provider_id: str | None = None) -> MagicMock:  # noqa: ARG001
        config = create_mock_config(player_id)
        if player_id == "group":
            config.default_name = "Group"
            config.get_value.side_effect = lambda key, default=None: {
                CONF_DYNAMIC_GROUP_MEMBERS: True,
                CONF_GROUP_MEMBERS: [],
            }.get(key, default)
        return config

    def call_later(
        delay: float,
        target: object,
        *args: object,
        task_id: str | None = None,
        **kwargs: object,
    ) -> MagicMock:
        assert task_id is not None
        timers[task_id] = (delay, target, args, kwargs)
        handle = MagicMock()
        handle.cancel.side_effect = lambda: timers.pop(task_id, None)
        return handle

    mass.config.get_base_player_config.side_effect = get_base_player_config
    mass.call_later.side_effect = call_later
    mass.cancel_timer.side_effect = lambda task_id: timers.pop(task_id, None)
    return mass, timers


@pytest.mark.asyncio
async def test_stop_cancels_pending_resume_timer_after_leader_switch() -> None:
    """Stopping the group must cancel the delayed resume queued during leader re-selection."""
    mass, timers = _create_mass()

    players = MagicMock()
    players._handle_cmd_stop = AsyncMock()
    players._handle_cmd_resume = AsyncMock()
    players.cmd_set_members = AsyncMock()
    players.trigger_player_update = MagicMock()
    players.get_plugin_sources.return_value = []

    async def wait_for_player_update(
        player_id: str, timeout: float, action: object  # noqa: ARG001
    ) -> None:
        await action

    players.wait_for_player_update.side_effect = wait_for_player_update
    mass.players = players

    leader_provider = MockProvider("dlna", instance_id="leader_provider", mass=mass)
    leader = MockPlayer(leader_provider, "leader", "Leader")
    leader._attr_group_members = ["leader", "member"]
    leader._attr_playback_state = PlaybackState.PLAYING
    leader.update_state(signal_event=False)

    member = MockPlayer(leader_provider, "member", "Member")
    member.update_state(signal_event=False)

    group_provider = MockProvider("sync_group", instance_id="sync_group", mass=mass)
    group = SyncGroupPlayer(group_provider, "group")
    await group.on_config_updated()
    group.sync_leader = leader
    group._attr_group_members = ["leader", "member"]
    group._attr_active_source = "queue"

    player_map = {
        "group": group,
        "leader": leader,
        "member": member,
    }
    players.get_player.side_effect = lambda player_id, *_args: player_map.get(player_id)
    players.all_players.return_value = list(player_map.values())

    await group.set_members(player_ids_to_remove=["leader"])

    resume_task_id = "syncgroup_resume_group"
    assert resume_task_id in timers

    await group.stop()

    assert resume_task_id not in timers
    players._handle_cmd_resume.assert_not_awaited()
