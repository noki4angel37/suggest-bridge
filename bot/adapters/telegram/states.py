"""FSM states shared by the Telegram adapter handlers."""

from aiogram.fsm.state import State, StatesGroup


class DraftFlow(StatesGroup):
    editing_text = State()


class AdminReject(StatesGroup):
    waiting_reason = State()


class AdminReply(StatesGroup):
    waiting_text = State()


class AdminSchedule(StatesGroup):
    waiting_datetime = State()


class AdminEdit(StatesGroup):
    waiting_text = State()
