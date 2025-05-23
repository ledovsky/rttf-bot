import datetime
import os

import sqlalchemy as sa

import utils.settings as settings_mod
from bot.notifications import send_player_update
from db.models import Base, DBSubscription, DBTournament, DBUserConfig
from db.session_factory import SessionLocal
from services.player_service import PlayerService

# Override DB settings for testing (using in-memory SQLite)
TEST_DATABASE_URL = 'sqlite:///:memory:'

settings_mod.settings.DB_URL = TEST_DATABASE_URL


class PlayerServiceNotFull(PlayerService):
    """Парсит страницу, где часть турниров удалено"""

    def _get_tournaments_pages(self):
        pages = []
        path = 'htmls/2025-04-12/tournaments/not_full_list.html'
        if os.path.isfile(path):
            with open(path, 'r', encoding='utf-8') as f:
                pages.append(f.read())
        return pages


class PlayerServiceFull(PlayerService):
    """Парсит полную страницу с турнирами"""

    def _get_tournaments_pages(self):
        pages = []
        path = 'htmls/2025-04-12/tournaments/full_list.html'
        if os.path.isfile(path):
            with open(path, 'r', encoding='utf-8') as f:
                pages.append(f.read())
        return pages


def test_update_tournaments():
    # Подготовка тестовой базы sqlite
    engine = sa.create_engine(TEST_DATABASE_URL)
    Base.metadata.create_all(engine)
    SessionLocal.configure(bind=engine)
    with SessionLocal() as session:
        user_config = DBUserConfig(id=1, config={})
        session.add(user_config)
        # DBPlayer removed; players now represented as ints, so no need to add players.
        session.commit()

    # База пустая, ожидаем, что все турниры попадут в базу
    service = PlayerServiceNotFull()
    service.update_tournaments()
    with SessionLocal() as session:
        count = session.query(DBTournament).count()
    assert count == 48, f'Expected 48 tournaments after first update, got {count}'

    # В полном списке на 3 турнира больше. Ожидаем, что в базу попадут только они
    service = PlayerServiceFull()
    service.update_tournaments()
    with SessionLocal() as session:
        count = session.query(DBTournament).count()
    assert count == 51, f'Expected 51 tournaments after second update, got {count}'


class SendUpdateMockMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.messages = []

    def _send_player_update(self, user_id, info):
        self.messages.append(send_player_update(user_id, info, bot_context=None))


class TournamentPlayerService(SendUpdateMockMixin, PlayerService):
    def _get_tournament_page(self, tournament_id):
        if tournament_id == 168138:
            with open('htmls/2025-04-12/tournament/168138.html', 'r') as f:
                return f.read()
        if tournament_id == 168577:
            with open('htmls/2025-04-12/tournament/168577.html', 'r') as f:
                return f.read()
        if tournament_id == 123:
            return ''
        raise ValueError('Expected on of predifined tournament ids')


def test_update_player_tournaments():
    # Подготовка тестовой базы
    engine = sa.create_engine(TEST_DATABASE_URL)
    Base.metadata.create_all(engine)
    # Reconfigure SessionLocal to use the test engine
    SessionLocal.configure(bind=engine)
    with SessionLocal() as session:
        user_config = DBUserConfig(id=1, config={})
        session.add(user_config)
        t1 = DBTournament(
            id=168138,
            tournament_date=datetime.date(2025, 4, 5),
            info_json='{}',
            next_update_dtm=datetime.datetime(2025, 4, 12, 22, 0, 0).timestamp(),
        )
        t2 = DBTournament(
            id=168577,
            tournament_date=datetime.date(2025, 4, 13),
            info_json='{}',
            next_update_dtm=datetime.datetime(2025, 4, 12, 22, 0, 0).timestamp(),
        )
        session.add_all([t1, t2])
        # DBPlayer removed; players now represented as ints, so no need to add players.
        session.commit()

    players = [124031, 84962, 107011]
    tournament_id = 168138
    service = TournamentPlayerService()
    updated, is_ok = service._update_player_tournaments(players, tournament_id)
    assert is_ok
    assert set(updated.keys()) == {124031, 84962}
    assert updated[124031].status == 'completed'
    assert updated[124031].games_won == 3
    assert (updated[124031].rating_delta - 9.0) < 0.001

    tournament_id = 168577
    service = TournamentPlayerService()
    updated, is_ok = service._update_player_tournaments(players, tournament_id)
    assert is_ok
    assert set(updated.keys()) == {124031, 107011}
    assert updated[107011].status == 'registered'
    assert updated[124031].status == 'refused'

    tournament_id = 123
    service = TournamentPlayerService()
    updated, is_ok = service._update_player_tournaments(players, tournament_id)
    assert not is_ok


def test_process_batch_and_notify():
    # Подготовка тестовой базы
    engine = sa.create_engine(TEST_DATABASE_URL)
    Base.metadata.create_all(engine)
    # Reconfigure SessionLocal to use the test engine
    SessionLocal.configure(bind=engine)
    with SessionLocal() as session:
        user_config = DBUserConfig(id=1, config={})
        session.add(user_config)
        sub1 = DBSubscription(user_id=1, player_id=124031)
        sub2 = DBSubscription(user_id=1, player_id=84962)
        session.add_all([sub1, sub2])
        t1 = DBTournament(
            id=168138,
            tournament_date=datetime.date(2025, 4, 5),
            info_json='{}',
            next_update_dtm=datetime.datetime(2025, 4, 12, 22, 0, 0).timestamp(),
        )
        t2 = DBTournament(
            id=168577,
            tournament_date=datetime.date(2025, 4, 13),
            info_json='{}',
            next_update_dtm=datetime.datetime(2025, 4, 12, 22, 0, 0).timestamp(),
        )
        session.add_all([t1, t2])
        session.commit()

    now = datetime.datetime(2025, 4, 12, 23, 0, 0)
    service = TournamentPlayerService()
    service.process_batch_and_notify(batch_size=10, now=now)
    assert (
        len(service.messages) == 3
    ), f'Expected 3 messages for completed tournament, got {len(service.messages)}'

    with SessionLocal() as session:
        tournament = (
            session.query(DBTournament).filter(DBTournament.id == 168138).first()
        )
        assert tournament.next_update_dtm is None
        tournament = (
            session.query(DBTournament).filter(DBTournament.id == 168577).first()
        )
        expected = datetime.datetime(2025, 4, 12, 23, 0, 0).timestamp() + 7200
        assert abs(tournament.next_update_dtm - expected) < 1.0
