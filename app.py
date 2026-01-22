import logging
import os
import random
import traceback
from collections import defaultdict
from datetime import datetime

from flask import Flask, abort, jsonify, render_template, request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from checkout import find_checkout

# Basic logging setup for debugging endpoints and important events.
# In production you may wish to configure logging differently (file handler, level via env, etc).
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static", template_folder="templates")
base_dir = os.path.abspath(os.path.dirname(__file__))
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(base_dir, "darts.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


# Models
class Profile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Settings(db.Model):
    """
    Simple single-row settings table to persist small client preferences and the
    last active game id so the frontend can restore an in-progress game after reload.
    """

    id = db.Column(db.Integer, primary_key=True)
    # Persist the last active (not-finished) game id for client restore
    last_active_game_id = db.Column(db.Integer, nullable=True)
    # Sound preferences (mirrors window.soundSettings on client)
    sound_enabled = db.Column(db.Boolean, default=True)
    sound_volume = db.Column(db.Float, default=1.0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "last_active_game_id": self.last_active_game_id,
            "sound_enabled": bool(self.sound_enabled),
            "sound_volume": float(self.sound_volume) if self.sound_volume is not None else 1.0,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Game(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    mode = db.Column(db.String(50), default="501")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Match/leg/set configuration and state
    # Number of legs a player must win to take a set (0 or None means no leg/set counting)
    legs_to_win = db.Column(db.Integer, nullable=True, default=3)
    # Number of sets a player must win to take the match (0 or None means no sets)
    sets_to_win = db.Column(db.Integer, nullable=True, default=1)
    # Current set and leg counters (1-based)
    current_set = db.Column(db.Integer, default=1)
    current_leg = db.Column(db.Integer, default=1)

    # How the first thrower for the first leg is determined: 'random' or 'bulls'
    first_throw_method = db.Column(db.String(16), default="random")

    # The index (0-based) of the player who will start the current leg within game.players order.
    # This is persisted so rotation works across reloads.
    current_start_index = db.Column(db.Integer, default=0)
    # Index (0-based) of the currently active player within game.players order.
    # Persisting this allows the frontend to restore which player is to throw next after a reload.
    current_active_index = db.Column(db.Integer, default=0)

    # Whether the game/match is finished
    finished = db.Column(db.Boolean, default=False)

    players = db.relationship("Player", backref="game", cascade="all, delete-orphan")
    # Historical sets recorded for this game (see MatchSet -> Leg)
    sets = db.relationship("MatchSet", backref="game", cascade="all, delete-orphan")


class Player(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), default="Player")
    starting_score = db.Column(db.Integer, default=501)
    current_score = db.Column(db.Integer, default=501)
    profile_id = db.Column(db.Integer, db.ForeignKey("profile.id"), nullable=True)
    profile = db.relationship("Profile", backref="game_players")
    game_id = db.Column(db.Integer, db.ForeignKey("game.id"))

    # Per-game counters for legs/sets (ephemeral to this Player instance which is tied to a Game)
    leg_wins = db.Column(db.Integer, default=0)
    set_wins = db.Column(db.Integer, default=0)

    # relationship to throws in this game
    throws = db.relationship("Throw", backref="player", cascade="all, delete-orphan")


class Throw(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey("player.id"))
    profile_id = db.Column(db.Integer, db.ForeignKey("profile.id"), nullable=True)
    value = db.Column(db.Integer)
    multiplier = db.Column(db.Integer)  # 0 = OUT/miss, 1 single, 2 double, 3 triple
    x = db.Column(db.Float, nullable=True)  # normalized x (0..1)
    y = db.Column(db.Float, nullable=True)  # normalized y (0..1)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


# Match history models
class MatchSet(db.Model):
    """
    Represents a set within a Game. Each MatchSet can contain multiple Leg records.
    """

    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(db.Integer, db.ForeignKey("game.id"))
    set_number = db.Column(db.Integer, default=1)  # 1-based
    winner_player_id = db.Column(db.Integer, db.ForeignKey("player.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    legs = db.relationship("Leg", backref="match_set", cascade="all, delete-orphan")


class Leg(db.Model):
    """
    Represents a single leg inside a MatchSet. Records the winner of the leg and its number.
    """

    id = db.Column(db.Integer, primary_key=True)
    match_set_id = db.Column(db.Integer, db.ForeignKey("match_set.id"))
    leg_number = db.Column(db.Integer, default=1)  # 1-based within the set
    winner_player_id = db.Column(db.Integer, db.ForeignKey("player.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# Ensure tables exist
with app.app_context():
    # Create any missing tables from the current models
    db.create_all()

    # Runtime compatibility step for older SQLite databases:
    # If the DB was created with an older schema it may be missing the
    # `profile_id` columns on `player` and `throw`. SQLite supports
    # adding simple columns via ALTER TABLE ... ADD COLUMN, so attempt
    # to add them if they're absent. We swallow errors so the app can
    # still start even if this compatibility step can't run for some reason.
    #
    # Note: this does not (and cannot, easily) add foreign-key constraints
    # to an existing SQLite table; it only adds the integer column so the
    # ORM can read/write it going forward.
    try:
        engine = db.get_engine()
        if engine.dialect.name == "sqlite":
            conn = engine.connect()
            try:

                def _has_column(table, col):
                    # PRAGMA table_info returns rows like: (cid, name, type, notnull, dflt_value, pk)
                    res = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
                    return any(row[1] == col for row in res)

                if not _has_column("player", "profile_id"):
                    conn.execute("ALTER TABLE player ADD COLUMN profile_id INTEGER")
                if not _has_column("throw", "profile_id"):
                    # The table name 'throw' is simple, but quote it to be safe
                    conn.execute('ALTER TABLE "throw" ADD COLUMN profile_id INTEGER')
                # Backfill for newer game column added to persist the active player index across reloads
                # Older DBs may be missing this column; add a simple integer defaulting to 0.
                if not _has_column("game", "current_active_index"):
                    try:
                        conn.execute("ALTER TABLE game ADD COLUMN current_active_index INTEGER DEFAULT 0")
                    except Exception:
                        # Best-effort only; ignore failures to allow app to start with existing DB
                        pass
            finally:
                conn.close()
    except Exception:
        # If anything goes wrong here we intentionally ignore it so the
        # application can continue using the existing DB. The error can
        # be investigated separately (logs/console) if needed.
        pass


# Helpers
def ensure_schema_compatibility():
    """
    Ensure commonly added columns/tables exist for older SQLite DBs:
      - player.profile_id, player.leg_wins, player.set_wins
      - throw.profile_id, throw.x, throw.y
      - game.* columns added for match/leg support (legs_to_win, sets_to_win, first_throw_method, etc.)
      - match_set and leg tables will be created by SQLAlchemy's create_all() for new installs.

    This function attempts ALTER TABLE ... ADD COLUMN for missing simple columns on SQLite.
    It's intentionally defensive and best-effort; use proper migrations for production.
    """
    try:
        try:
            engine = db.get_engine()
        except Exception:
            engine = db.engine
        # Only run for SQLite
        if engine and getattr(engine, "dialect", None) and engine.dialect.name == "sqlite":
            conn = engine.connect()
            try:

                def _has_column(table, col):
                    res = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
                    return any(row[1] == col for row in res)

                # player columns
                if _has_column("player", "profile_id") is False:
                    try:
                        conn.execute("ALTER TABLE player ADD COLUMN profile_id INTEGER")
                    except Exception:
                        pass
                if _has_column("player", "leg_wins") is False:
                    try:
                        conn.execute("ALTER TABLE player ADD COLUMN leg_wins INTEGER DEFAULT 0")
                    except Exception:
                        pass
                if _has_column("player", "set_wins") is False:
                    try:
                        conn.execute("ALTER TABLE player ADD COLUMN set_wins INTEGER DEFAULT 0")
                    except Exception:
                        pass

                # throw columns
                if _has_column("throw", "profile_id") is False:
                    try:
                        conn.execute('ALTER TABLE "throw" ADD COLUMN profile_id INTEGER')
                    except Exception:
                        pass
                if _has_column("throw", "x") is False:
                    try:
                        conn.execute('ALTER TABLE "throw" ADD COLUMN x REAL')
                    except Exception:
                        pass
                if _has_column("throw", "y") is False:
                    try:
                        conn.execute('ALTER TABLE "throw" ADD COLUMN y REAL')
                    except Exception:
                        pass

                # game columns (added to support newer match/leg/set fields)
                # These columns are simple and nullable/defaulted so ALTER is safe on SQLite.
                if _has_column("game", "legs_to_win") is False:
                    try:
                        conn.execute("ALTER TABLE game ADD COLUMN legs_to_win INTEGER")
                    except Exception:
                        pass
                if _has_column("game", "sets_to_win") is False:
                    try:
                        conn.execute("ALTER TABLE game ADD COLUMN sets_to_win INTEGER")
                    except Exception:
                        pass
                if _has_column("game", "current_set") is False:
                    try:
                        conn.execute("ALTER TABLE game ADD COLUMN current_set INTEGER DEFAULT 1")
                    except Exception:
                        pass
                if _has_column("game", "current_leg") is False:
                    try:
                        conn.execute("ALTER TABLE game ADD COLUMN current_leg INTEGER DEFAULT 1")
                    except Exception:
                        pass
                if _has_column("game", "first_throw_method") is False:
                    try:
                        conn.execute("ALTER TABLE game ADD COLUMN first_throw_method VARCHAR(32) DEFAULT 'random'")
                    except Exception:
                        pass
                if _has_column("game", "current_start_index") is False:
                    try:
                        conn.execute("ALTER TABLE game ADD COLUMN current_start_index INTEGER DEFAULT 0")
                    except Exception:
                        pass
                # Add current_active_index to allow persisting/restoring which player is to throw next.
                if _has_column("game", "current_active_index") is False:
                    try:
                        conn.execute("ALTER TABLE game ADD COLUMN current_active_index INTEGER DEFAULT 0")
                    except Exception:
                        pass
                if _has_column("game", "finished") is False:
                    try:
                        # SQLite does not have native BOOLEAN type - use INTEGER default 0
                        conn.execute("ALTER TABLE game ADD COLUMN finished INTEGER DEFAULT 0")
                    except Exception:
                        pass

            finally:
                conn.close()
    except Exception:
        # Best-effort only; migrations are recommended for production.
        pass


def profile_to_dict(p: Profile):
    return {"id": p.id, "name": p.name, "created_at": p.created_at.isoformat()}


# Routes - front page
@app.route("/")
def index():
    return render_template("index.html")


# Profile APIs
@app.route("/api/profiles", methods=["GET", "POST"])
def profiles():
    if request.method == "GET":
        profs = Profile.query.order_by(Profile.name).all()
        return jsonify([profile_to_dict(p) for p in profs])
    else:
        data = request.json or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Name required"}), 400
        existing = Profile.query.filter_by(name=name).first()
        if existing:
            return jsonify({"error": "Profile name already exists", "id": existing.id}), 400
        try:
            p = Profile(name=name)
            db.session.add(p)
            db.session.commit()
            return jsonify(profile_to_dict(p)), 201
        except Exception as e:
            # rollback any partial transaction and return a JSON error
            try:
                db.session.rollback()
            except:
                pass
            return jsonify({"error": "Failed to create profile", "details": str(e)}), 500


@app.route("/api/profiles/<int:profile_id>", methods=["PATCH", "DELETE"])
def profile_modify(profile_id):
    profile = Profile.query.get_or_404(profile_id)
    if request.method == "PATCH":
        data = request.json or {}
        name = data.get("name")
        if name:
            name = name.strip()
            if not name:
                return jsonify({"error": "Invalid name"}), 400
            other = Profile.query.filter(Profile.name == name, Profile.id != profile.id).first()
            if other:
                return jsonify({"error": "Another profile with that name exists"}), 400
            profile.name = name
            db.session.commit()
        return jsonify(profile_to_dict(profile))
    else:
        # Delete profile: remove throws tied to profile and clear links from players
        Throw.query.filter_by(profile_id=profile.id).delete()
        players = Player.query.filter_by(profile_id=profile.id).all()
        for pl in players:
            pl.profile_id = None
        db.session.delete(profile)
        db.session.commit()
        return jsonify({"status": "deleted"})


@app.route("/api/profiles/<int:profile_id>/reset", methods=["POST"])
def profile_reset(profile_id):
    profile = Profile.query.get_or_404(profile_id)
    Throw.query.filter_by(profile_id=profile.id).delete()
    db.session.commit()
    return jsonify({"status": "reset"})


# Profile stats endpoint (new)
@app.route("/api/profiles/<int:profile_id>/stats", methods=["GET"])
def profile_stats(profile_id):
    """
    Returns aggregated profile statistics:
    - overall_avg_3dart
    - best_first9_avg_3dart (best single game's first-9 average in 3-dart format)
    - overall_first9_avg_3dart (average of per-game first-9 averages)
    - count_games (number of distinct games where this profile threw darts)
    - best_game_id (game id with the lowest total throws by this profile)
    - best_game_throws (number of throws in that best game)
    - overall_thrown_darts (total throws across all games)
    """
    profile = Profile.query.get_or_404(profile_id)

    # All throws tied to this profile (ordered)
    throws = Throw.query.filter_by(profile_id=profile.id).order_by(Throw.timestamp).all()
    overall_thrown_darts = len(throws)
    total_scored = sum(t.value * t.multiplier for t in throws)
    overall_avg_per_throw = (total_scored / overall_thrown_darts) if overall_thrown_darts else 0
    overall_avg_3dart = overall_avg_per_throw * 3

    # Group throws by game id (via t.player.game_id). Some throws may have player or game missing; skip None games.
    game_groups = defaultdict(list)
    for t in throws:
        game_id = None
        if t.player and t.player.game_id:
            game_id = t.player.game_id
        # Only count throws that can be associated with a game
        if game_id is not None:
            game_groups[game_id].append(t)

    # Per-game metrics: first-9 average (3-dart), total throws
    per_game_first9_3dart = []
    game_throw_counts = {}
    for gid, gthrows in game_groups.items():
        gthrows_sorted = sorted(gthrows, key=lambda x: x.timestamp)
        game_throw_counts[gid] = len(gthrows_sorted)
        first9 = gthrows_sorted[:9]
        if first9:
            sum_first9 = sum(tt.value * tt.multiplier for tt in first9)
            avg_per_throw_first9 = sum_first9 / len(first9)
            per_game_first9_3dart.append(avg_per_throw_first9 * 3)

    best_first9 = max(per_game_first9_3dart) if per_game_first9_3dart else 0
    overall_first9_avg = (sum(per_game_first9_3dart) / len(per_game_first9_3dart)) if per_game_first9_3dart else 0
    count_games = len(game_groups)

    # Best game: the game where this profile used the fewest throws (i.e., most efficient)
    best_game_id = None
    best_game_throws = None
    if game_throw_counts:
        # select game with min throws
        best_game_id = min(game_throw_counts, key=lambda k: game_throw_counts[k])
        best_game_throws = game_throw_counts[best_game_id]

    return jsonify(
        {
            "profile_id": profile.id,
            "name": profile.name,
            "overall_avg_3dart": round(overall_avg_3dart, 2),
            "best_first9_avg_3dart": round(best_first9, 2),
            "overall_first9_avg_3dart": round(overall_first9_avg, 2),
            "count_games": count_games,
            "best_game_id": best_game_id,
            "best_game_throws": best_game_throws,
            "overall_thrown_darts": overall_thrown_darts,
        }
    )


# Simple settings API so the frontend can persist/retrieve preferences and last-active-game
@app.route("/api/settings", methods=["GET", "POST"])
def settings_api():
    """
    GET: returns settings (single-row). If absent, returns sensible defaults.
    POST: accepts JSON to update fields:
      { "last_active_game_id": <int|null>, "sound_enabled": true/false, "sound_volume": 0.0..1.0 }
    """
    if request.method == "GET":
        s = Settings.query.first()
        if not s:
            # defaults
            return jsonify(
                {
                    "last_active_game_id": None,
                    "sound_enabled": True,
                    "sound_volume": 1.0,
                }
            )
        return jsonify(s.to_dict())

    data = request.json or {}
    try:
        s = Settings.query.first()
        if not s:
            s = Settings()
            db.session.add(s)
        if "last_active_game_id" in data:
            s.last_active_game_id = data.get("last_active_game_id")
        if "sound_enabled" in data:
            s.sound_enabled = bool(data.get("sound_enabled"))
        if "sound_volume" in data:
            # Safely handle None, numeric and string inputs without calling float(None).
            sv = data.get("sound_volume")
            try:
                if sv is None:
                    # explicit null -> reset to default 1.0
                    s.sound_volume = 1.0
                else:
                    # accept numeric or numeric-string; ValueError/TypeError handled below
                    s.sound_volume = max(0.0, min(1.0, float(sv)))
            except (ValueError, TypeError):
                # invalid value; ignore and leave existing value unchanged
                pass
        db.session.commit()
        return jsonify(s.to_dict())
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({"error": "Failed to update settings", "details": str(e)}), 500


# Game creation
@app.route("/api/new_game", methods=["POST"])
def new_game():
    payload = request.json or {}
    # Ensure schema is compatible before creating Player rows (helps with older SQLite DBs)
    ensure_schema_compatibility()

    # Diagnostic container to capture SQLite table_info (populated when possible)
    _schema_diag = {}
    try:
        # Try to gather schema info up-front for easier diagnostics if an error occurs.
        try:
            engine = db.get_engine()
        except Exception:
            engine = db.engine

        if engine and getattr(engine, "dialect", None) and engine.dialect.name == "sqlite":
            conn = None
            try:
                conn = engine.connect()
                try:
                    res_p = conn.execute(text("PRAGMA table_info('player')")).fetchall()
                    res_t = conn.execute(text("PRAGMA table_info('throw')")).fetchall()
                    _schema_diag["player"] = [r[1] for r in res_p]
                    _schema_diag["throw"] = [r[1] for r in res_t]
                    app.logger.debug("Schema diagnostic - player columns: %s", _schema_diag["player"])
                    app.logger.debug("Schema diagnostic - throw columns: %s", _schema_diag["throw"])
                except Exception as inner_pr:
                    # If PRAGMA fails, record the exception message
                    _schema_diag["error"] = f"failed to read PRAGMA: {inner_pr}"
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        mode = payload.get("mode", "501")
        players_input = payload.get("players", [])  # expected list of profile ids OR names
        players_input = players_input[:6]
        starting = 501 if mode in ("501", "301") else 0
        if mode == "301":
            starting = 301

        # Persist optional game options supplied by the client:
        # legs_to_win (int), sets_to_win (int), first_throw_method (string)
        legs_to_win = payload.get("legs_to_win")
        sets_to_win = payload.get("sets_to_win")
        first_method = payload.get("first_throw_method")

        game = Game(mode=mode)
        # apply provided options when present (best-effort parsing)
        if legs_to_win is not None:
            try:
                game.legs_to_win = int(legs_to_win)
            except Exception:
                # ignore invalid values and leave default
                pass
        if sets_to_win is not None:
            try:
                game.sets_to_win = int(sets_to_win)
            except Exception:
                pass
        if first_method is not None:
            try:
                game.first_throw_method = str(first_method)
            except Exception:
                pass

        db.session.add(game)
        db.session.flush()
        created_players = []
        for p in players_input:
            profile = None
            name = None
            if isinstance(p, dict):
                if "profile_id" in p:
                    profile = Profile.query.get(p["profile_id"])
                name = p.get("name") or (profile.name if profile else "Player")
            elif isinstance(p, int):
                profile = Profile.query.get(p)
                name = profile.name if profile else f"Player {p}"
            elif isinstance(p, str):
                name = p
                profile = Profile.query.filter_by(name=name).first()
            else:
                name = "Player"
            pl = Player(name=name, starting_score=starting, current_score=starting, game_id=game.id)
            if profile:
                pl.profile_id = profile.id
            db.session.add(pl)
            created_players.append({"id": pl.id, "name": name, "profile_id": pl.profile_id})
        db.session.commit()

        # Persist last-active game id so frontend can restore after reload
        try:
            s = Settings.query.first()
            if not s:
                s = Settings(last_active_game_id=game.id)
                db.session.add(s)
            else:
                s.last_active_game_id = game.id
            db.session.commit()
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass

        return jsonify({"game_id": game.id, "players_created": created_players})
    except Exception as e:
        # On error, ensure the transaction is rolled back and return a JSON error payload
        try:
            db.session.rollback()
        except Exception:
            pass

        # Attempt to capture schema again at the moment of error if we don't already have it
        try:
            engine = db.get_engine()
        except Exception:
            engine = db.engine

        if (not _schema_diag) and engine and getattr(engine, "dialect", None) and engine.dialect.name == "sqlite":
            try:
                conn = engine.connect()
                try:
                    res_p = conn.execute(text("PRAGMA table_info('player')")).fetchall()
                    res_t = conn.execute(text("PRAGMA table_info('throw')")).fetchall()
                    _schema_diag["player"] = [r[1] for r in res_p]
                    _schema_diag["throw"] = [r[1] for r in res_t]
                except Exception as inner_pr:
                    _schema_diag["error"] = f"failed to read PRAGMA at exception time: {inner_pr}"
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
            except Exception:
                # If even attempting to connect fails, note that
                _schema_diag["error"] = _schema_diag.get("error", "") + " (could not connect to engine for PRAGMA)"

        tb = traceback.format_exc()
        # Include schema diagnostics in the response to help debugging missing-column errors
        resp = {"error": "Failed to create game", "details": str(e), "trace": tb}
        if _schema_diag:
            resp["schema_diagnostics"] = _schema_diag
        return jsonify(resp), 500


# API: add a player (by profile or name) to an existing game
@app.route("/api/games/<int:game_id>/add_player", methods=["POST"])
def add_player_to_game(game_id):
    """
    Accepts JSON:
      { "profile_id": <int> }       # attach existing profile to the game
    or
      { "name": "<Player name>" }   # create a game-only player (or use profile if found)
    Returns 201 with created player info, or 4xx on error.
    """
    # Ensure schema is compatible before touching player/throw rows (helps with older SQLite DBs)
    ensure_schema_compatibility()
    game = Game.query.get_or_404(game_id)
    data = request.json or {}

    # limit players per game to 6
    if len(game.players) >= 6:
        return jsonify({"error": "Game already has maximum number of players (6)"}), 400

    profile = None
    profile_id = data.get("profile_id")
    name = data.get("name")

    if profile_id is not None:
        try:
            profile_id = int(profile_id)
        except Exception:
            return jsonify({"error": "Invalid profile_id"}), 400
        profile = Profile.query.get(profile_id)
        if not profile:
            return jsonify({"error": "Profile not found"}), 404
        # prefer provided name, otherwise profile name
        name = name or profile.name

    # If a name is provided but matches an existing profile and no explicit profile_id, link it
    if profile is None and name:
        existing = Profile.query.filter_by(name=name).first()
        if existing:
            profile = existing

    # fallback name
    if not name:
        name = "Player"

    # determine starting score based on game mode
    starting = 501 if game.mode in ("501", "301") else 0
    if game.mode == "301":
        starting = 301

    pl = Player(name=name, starting_score=starting, current_score=starting, game_id=game.id)
    if profile:
        pl.profile_id = profile.id

    db.session.add(pl)
    db.session.commit()

    created = {"id": pl.id, "name": pl.name, "profile_id": pl.profile_id, "current_score": pl.current_score}
    return jsonify({"player": created}), 201


# -- additional endpoints for frontend flows (starter selection & manual next-leg) --
@app.route("/api/games/<int:game_id>/set_starter", methods=["POST"])
def set_starter(game_id):
    """
    Set which player (by player_id) or which player index (starter_index) should start the current leg.
    Accepts JSON:
      { "player_id": <int> }       # sets the game's current_start_index to the index of that player
    or
      { "starter_index": <int> }   # sets the game's current_start_index directly (0-based)
    Returns 200 with updated start index on success.
    """
    game = Game.query.get_or_404(game_id)
    data = request.json or {}
    player_id = data.get("player_id")
    starter_index = data.get("starter_index")

    if player_id is not None:
        try:
            player_id = int(player_id)
        except Exception:
            return jsonify({"error": "Invalid player_id"}), 400
        # find index of that player in the game's players list (preserve order)
        index = None
        for idx, p in enumerate(game.players):
            if p.id == player_id:
                index = idx
                break
        if index is None:
            return jsonify({"error": "Player not part of this game"}), 404
        game.current_start_index = index
    elif starter_index is not None:
        try:
            si = int(starter_index)
        except Exception:
            return jsonify({"error": "Invalid starter_index"}), 400
        if si < 0 or (len(game.players) and si >= len(game.players)):
            return jsonify({"error": "starter_index out of range"}), 400
        game.current_start_index = si
    else:
        return jsonify({"error": "player_id or starter_index required"}), 400

    db.session.add(game)
    db.session.commit()
    return jsonify({"status": "ok", "current_start_index": game.current_start_index})


@app.route("/api/games/<int:game_id>/set_active", methods=["POST"])
def set_active_player(game_id):
    """
    Set which player is currently active for this game.
    Accepts JSON:
      { "player_id": <int> }       # sets current_active_index to the index of that player
    or
      { "active_index": <int> }    # set index directly (0-based)
    Returns 200 with updated current_active_index on success.
    """
    game = Game.query.get_or_404(game_id)
    data = request.json or {}
    player_id = data.get("player_id")
    active_index = data.get("active_index")

    if player_id is not None:
        try:
            player_id = int(player_id)
        except Exception:
            return jsonify({"error": "Invalid player_id"}), 400
        # find index of that player in the game's players list (preserve order)
        index = None
        for idx, p in enumerate(game.players):
            if p.id == player_id:
                index = idx
                break
        if index is None:
            return jsonify({"error": "Player not part of this game"}), 404
        game.current_active_index = index
    elif active_index is not None:
        try:
            ai = int(active_index)
        except Exception:
            return jsonify({"error": "Invalid active_index"}), 400
        if ai < 0 or (len(game.players) and ai >= len(game.players)):
            return jsonify({"error": "active_index out of range"}), 400
        game.current_active_index = ai
    else:
        return jsonify({"error": "player_id or active_index required"}), 400

    db.session.add(game)
    db.session.commit()
    return jsonify({"status": "ok", "current_active_index": game.current_active_index})


@app.route("/api/games/<int:game_id>/next_leg", methods=["POST"])
def next_leg(game_id):
    """
    Manually advance to the next leg. This will:
      - reset all players' current_score to their starting_score
      - increment game.current_leg
      - rotate game.current_start_index by +1 modulo player count (to rotate starter)
    Returns the updated leg and start index.
    """
    game = Game.query.get_or_404(game_id)

    # If match already finished, disallow advancing
    if getattr(game, "finished", False):
        return jsonify({"error": "Game already finished"}), 400

    # Reset all player's scores to their starting score
    for pl in game.players:
        pl.current_score = pl.starting_score
        # leave per-player leg_wins/set_wins as-is; front-end will refresh these values

    # Increment leg counter
    game.current_leg = (game.current_leg or 1) + 1

    # Rotate starting index
    count = len(game.players) if game.players is not None else 0
    if count:
        game.current_start_index = ((game.current_start_index or 0) + 1) % count
        # set the active player to the rotated start index so client reloads restore the correct active player
        try:
            game.current_active_index = game.current_start_index
        except Exception:
            # best-effort: ignore if column missing / invalid
            pass

    db.session.add(game)
    db.session.commit()
    return jsonify({"status": "ok", "current_leg": game.current_leg, "current_start_index": game.current_start_index})


# Endpoint: restart game (reset scores/leg/set counters and make game active)
@app.route("/api/games/<int:game_id>/restart", methods=["POST"])
def restart_game(game_id):
    """
    Restart the game: reset all players' current_score to their starting_score,
    reset per-player leg_wins/set_wins counters to zero, reset current_set/current_leg
    to the beginning and mark the game as active (finished=False).
    """
    logger.info("Restart requested for game id=%s", game_id)
    try:
        game = Game.query.get_or_404(game_id)

        logger.info("Resetting %d players for game id=%s", len(game.players or []), game_id)
        # Reset per-player scores and counters
        for pl in game.players:
            pl.current_score = pl.starting_score
            try:
                pl.leg_wins = 0
            except Exception:
                # older DBs may not have these columns; ignore if absent
                logger.debug("Player %s missing leg_wins column", getattr(pl, "id", "<unknown>"))
                pass
            try:
                pl.set_wins = 0
            except Exception:
                logger.debug("Player %s missing set_wins column", getattr(pl, "id", "<unknown>"))
                pass
            db.session.add(pl)

        # Reset game-level counters and mark active
        try:
            game.current_set = 1
        except Exception:
            logger.debug("Game missing current_set column")
            pass
        try:
            game.current_leg = 1
        except Exception:
            logger.debug("Game missing current_leg column")
            pass
        try:
            game.current_start_index = 0
        except Exception:
            logger.debug("Game missing current_start_index column")
            pass

        # reset active player index to the starting index on restart
        try:
            game.current_active_index = getattr(game, "current_start_index", 0)
        except Exception:
            game.current_active_index = 0

        game.finished = False

        # Remove any Throw rows for players in this game so last-visit UI clears.
        # This ensures that after a restart there are no lingering throws shown as the
        # player's "last visit". Use a bulk delete for efficiency and avoid loading rows.
        try:
            player_ids = [p.id for p in (game.players or []) if getattr(p, "id", None) is not None]
            if player_ids:
                Throw.query.filter(Throw.player_id.in_(player_ids)).delete(synchronize_session=False)
        except Exception:
            logger.exception("Failed to delete throws for game id=%s during restart", game_id)

        db.session.add(game)
        db.session.commit()
        logger.info(
            "Game id=%s restarted successfully (current_active_index=%s)",
            game_id,
            getattr(game, "current_active_index", None),
        )
        return jsonify({"status": "ok", "message": "Game restarted"})
    except Exception as e:
        # Log stack trace for easier debugging
        logger.exception("Failed to restart game id=%s: %s", game_id, e)
        try:
            db.session.rollback()
        except Exception:
            logger.debug("Rollback failed after restart error for game id=%s", game_id)
        return jsonify({"error": "Failed to restart game"}), 500


# Endpoint: end game (mark game finished/inactive)
# Provide two routes for compatibility: /end and /end_game
@app.route("/api/games/<int:game_id>/end", methods=["POST"])
@app.route("/api/games/<int:game_id>/end_game", methods=["POST"])
def end_game(game_id):
    """
    Mark the game as finished/inactive. When finished, throws are rejected by the server.
    """
    logger.info("End game requested for game id=%s", game_id)
    try:
        game = Game.query.get_or_404(game_id)
        game.finished = True
        db.session.add(game)
        db.session.commit()
        logger.info("Game id=%s marked finished", game_id)

        # If this game was the recorded last_active_game_id, clear it so frontend won't try to restore a finished game
        try:
            s = Settings.query.first()
            if s and s.last_active_game_id == game.id:
                logger.info(
                    "Clearing last_active_game_id (was %s) due to game end for game id=%s",
                    s.last_active_game_id,
                    game_id,
                )
                s.last_active_game_id = None
                db.session.add(s)
                db.session.commit()
        except Exception as inner_e:
            logger.exception("Failed to clear last_active_game_id after ending game id=%s: %s", game_id, inner_e)
            try:
                db.session.rollback()
            except Exception:
                logger.debug("Rollback failed after clearing last_active_game_id for game id=%s", game_id)

        return jsonify({"status": "ok", "message": "Game ended"})
    except Exception as e:
        logger.exception("Failed to end game id=%s: %s", game_id, e)
        try:
            db.session.rollback()
        except Exception:
            logger.debug("Rollback failed after end_game error for game id=%s", game_id)
        return jsonify({"error": "Failed to end game"}), 500


# Game state (includes per-player last visit hits and stats, legs/sets state and match history)
@app.route("/api/game_state/<int:game_id>", methods=["GET"])
def game_state(game_id):
    # Attempt to load the game row. Missing columns on older SQLite DBs can raise
    # an OperationalError (e.g. "no such column: game.current_active_index").
    # In that case, try a best-effort schema compatibility step and retry once.
    try:
        game = Game.query.get_or_404(game_id)
    except OperationalError:
        # Perform compatibility fixes (ALTER TABLE ... ADD COLUMN where possible)
        # then retry the query once. If this still fails the exception will propagate.
        try:
            ensure_schema_compatibility()
        except Exception:
            # If the compatibility step itself fails, re-raise to keep behavior unchanged.
            raise
        game = Game.query.get_or_404(game_id)
    players_out = []
    # produce per-player view
    for p in game.players:
        # last visit (up to 3 throws)
        throws = Throw.query.filter_by(player_id=p.id).order_by(Throw.timestamp.desc()).limit(3).all()
        throws_chrono = list(reversed(throws))
        last_visit_score = sum(t.value * t.multiplier for t in throws_chrono) if throws_chrono else 0
        last_visit_hits = []
        for t in throws_chrono:
            if t.value == 25 and t.multiplier == 2:
                label = "BULL"
            elif t.value == 25:
                label = "SBULL"
            else:
                prefix = "T" if t.multiplier == 3 else ("D" if t.multiplier == 2 else "S")
                label = f"{prefix}{t.value}"
            hit = {
                "value": t.value,
                "multiplier": t.multiplier,
                "label": label,
                "x": t.x,
                "y": t.y,
                "timestamp": t.timestamp.isoformat() if t.timestamp else None,
            }
            last_visit_hits.append(hit)
        # compute stats (profile-backed if present, otherwise per-player)
        total_scored = 0
        throw_count = 0
        avg_per_throw = 0
        avg_3dart = 0
        first9_avg_3dart = 0
        if p.profile_id:
            profile_throws = Throw.query.filter_by(profile_id=p.profile_id).order_by(Throw.timestamp).all()
            total_scored = sum(t.value * t.multiplier for t in profile_throws)
            throw_count = len(profile_throws)
            avg_per_throw = (total_scored / throw_count) if throw_count else 0
            avg_3dart = avg_per_throw * 3
            first9 = profile_throws[:9]
            sum_first9 = sum(t.value * t.multiplier for t in first9)
            n_first9 = len(first9)
            first9_avg_3dart = (sum_first9 / n_first9 * 3) if n_first9 else 0
        else:
            all_throws = Throw.query.filter_by(player_id=p.id).order_by(Throw.timestamp).all()
            total_scored = sum(t.value * t.multiplier for t in all_throws)
            throw_count = len(all_throws)
            avg_per_throw = (total_scored / throw_count) if throw_count else 0
            avg_3dart = avg_per_throw * 3
            first9 = all_throws[:9]
            sum_first9 = sum(t.value * t.multiplier for t in first9)
            n_first9 = len(first9)
            first9_avg_3dart = (sum_first9 / n_first9 * 3) if n_first9 else 0

        players_out.append(
            {
                "id": p.id,
                "name": p.name,
                "profile_id": p.profile_id,
                "current_score": p.current_score,
                "starting_score": p.starting_score,
                "total_scored": total_scored,
                "throw_count": throw_count,
                "avg_per_throw": round(avg_per_throw, 2),
                "avg_3dart": round(avg_3dart, 1),
                "first9_avg_3dart": round(first9_avg_3dart, 1),
                "last_visit_score": last_visit_score,
                "last_visit_hits": last_visit_hits,
                "suggestion": (
                    find_checkout(p.current_score)
                    if (p.current_score <= 170 and p.current_score > 0 and str(game.mode).lower().endswith("01"))
                    else None
                ),
                "leg_wins": getattr(p, "leg_wins", 0),
                "set_wins": getattr(p, "set_wins", 0),
            }
        )

    # Build match-level info and history
    history = []
    sets = MatchSet.query.filter_by(game_id=game.id).order_by(MatchSet.set_number).all()
    for s in sets:
        legs = []
        for lg in sorted(s.legs, key=lambda L: L.leg_number):
            legs.append({"leg_number": lg.leg_number, "winner_player_id": lg.winner_player_id})
        history.append({"set_number": s.set_number, "winner_player_id": s.winner_player_id, "legs": legs})

    game_info = {
        "id": game.id,
        "mode": game.mode,
        "legs_to_win": game.legs_to_win,
        "sets_to_win": game.sets_to_win,
        "current_set": game.current_set,
        "current_leg": game.current_leg,
        "first_throw_method": game.first_throw_method,
        "current_start_index": getattr(game, "current_start_index", 0),
        # Expose the persisted currently active player index so the client can restore currentTurn
        "current_active_index": getattr(game, "current_active_index", getattr(game, "current_start_index", 0)),
        "finished": bool(game.finished),
    }

    return jsonify({"game": game_info, "players": players_out, "history": history})


# Throw API - accepts optional normalized x,y and records profile_id if player has one
@app.route("/api/throw", methods=["POST"])
def register_throw():
    """
    Register a single dart throw. This endpoint now handles:
      - X01-style games (301/501/701/...) including leg/set management
      - Cricket and other modes are left to existing logic (no change here yet)
    Returns structured JSON statuses:
      - ok: normal throw recorded
      - bust: throw recorded but busted
      - invalid_finish_needs_double: finish invalid (needs double)
      - leg_won: a leg was won (includes updated leg/set counters)
      - set_won: a set was won
      - match_won: match finished (winner)
    """
    data = request.json or {}
    player_id = data.get("player_id")
    if player_id is None:
        return jsonify({"error": "player_id required"}), 400

    value = int(data.get("value", 0))
    multiplier = int(data.get("multiplier", 0))
    x = data.get("x")
    y = data.get("y")

    # Ensure DB schema compatibility (best-effort) before manipulating new columns
    ensure_schema_compatibility()

    player = Player.query.get_or_404(player_id)
    game = player.game

    # If the game has been marked finished, do not accept throws.
    # This prevents continuing to record throws after a match has been ended.
    if getattr(game, "finished", False):
        return jsonify({"error": "Game finished; no further throws accepted"}), 400

    scored = value * multiplier
    new_score = player.current_score - scored

    # record throw
    t = Throw(player_id=player.id, value=value, multiplier=multiplier)
    if x is not None and y is not None:
        try:
            t.x = float(x)
            t.y = float(y)
        except Exception:
            t.x = None
            t.y = None
    if player.profile_id:
        t.profile_id = player.profile_id

    # Helper: reset scores for a new leg
    def _reset_for_new_leg():
        # reset all player's current scores to their starting_score
        for pl in Game.query.get(game.id).players:
            pl.current_score = pl.starting_score
        # increment leg counter on game
        game.current_leg = (game.current_leg or 1) + 1
        # rotate start index (advance by 1 modulo player count)
        try:
            count = len(game.players)
        except Exception:
            count = len(Game.query.get(game.id).players)
        if count:
            game.current_start_index = ((game.current_start_index or 0) + 1) % count
            # when a new leg starts, ensure the active player index follows the current_start_index
            try:
                game.current_active_index = game.current_start_index
            except Exception:
                pass

    # Helper: start a new set
    def _start_new_set():
        # reset per-player leg_wins to 0 and increment current_set
        for pl in Game.query.get(game.id).players:
            pl.leg_wins = 0
        game.current_set = (game.current_set or 1) + 1
        game.current_leg = 1
        # reset start index back to 0 or leave rotation behavior as-is; keep rotation consistent
        # (we do not override current_start_index here; it continues rotating)
        # create a new MatchSet record (history)
        ms = MatchSet(game_id=game.id, set_number=game.current_set)
        db.session.add(ms)
        db.session.flush()
        return ms

    # X01-style modes - check for busts/finishes
    if str(game.mode).lower().endswith("01") or game.mode in ("501", "301", "701"):
        # bust conditions
        if new_score < 0 or new_score == 1:
            db.session.add(t)
            db.session.commit()
            return jsonify({"status": "bust", "current_score": player.current_score})

        if new_score == 0:
            # must finish on a double (or double bull 50)
            if multiplier != 2 and scored != 50:
                db.session.add(t)
                db.session.commit()
                return jsonify({"status": "invalid_finish_needs_double", "current_score": player.current_score})

            # valid finish -> record throw and process leg/set/match transitions
            player.current_score = 0
            db.session.add(t)
            # increment leg wins for this player
            player.leg_wins = (player.leg_wins or 0) + 1

            # persist changes and create leg record in history
            # ensure we have a MatchSet for the current game.current_set
            ms = MatchSet.query.filter_by(game_id=game.id, set_number=game.current_set).first()
            if not ms:
                ms = MatchSet(game_id=game.id, set_number=game.current_set)
                db.session.add(ms)
                db.session.flush()

            # record the leg
            leg_number = (len(ms.legs) + 1) if ms.legs is not None else 1
            leg_record = Leg(match_set_id=ms.id, leg_number=leg_number, winner_player_id=player.id)
            db.session.add(leg_record)

            # Evaluate whether this leg win also wins the set
            leg_threshold = game.legs_to_win or 0
            set_threshold = game.sets_to_win or 0

            resp = {
                "status": "leg_won",
                "player_id": player.id,
                "player_name": player.name,
                "leg_wins": player.leg_wins,
                "set_wins": player.set_wins,
                "current_set": game.current_set,
                "current_leg": game.current_leg,
            }

            # If legs_to_win is configured and reached, award a set
            if leg_threshold and player.leg_wins >= leg_threshold:
                # award set
                player.set_wins = (player.set_wins or 0) + 1
                ms.winner_player_id = player.id
                resp["status"] = "set_won"
                resp["set_wins"] = player.set_wins

                # reset leg counters for next set and persist set history
                # start a new set (which also resets leg_wins)
                _start_new_set()

                # If sets_to_win configured and reached -> match won
                if set_threshold and player.set_wins >= set_threshold:
                    game.finished = True
                    db.session.add(game)
                    db.session.add(player)
                    db.session.commit()
                    return jsonify(
                        {
                            "status": "match_won",
                            "player_id": player.id,
                            "player_name": player.name,
                            "set_wins": player.set_wins,
                            "message": f"{player.name} has won the match!",
                        }
                    )

                db.session.add(game)
                db.session.add(player)
                db.session.commit()
                return jsonify(
                    {
                        "status": "set_won",
                        "player_id": player.id,
                        "player_name": player.name,
                        "set_wins": player.set_wins,
                        "message": f"{player.name} has won set {game.current_set - 1}.",
                    }
                )

            # Otherwise just finish the leg and start next leg
            # Reset all players' current_score for the next leg
            _reset_for_new_leg()
            db.session.add(game)
            db.session.add(player)
            db.session.commit()

            return jsonify(
                {
                    "status": "leg_won",
                    "player_id": player.id,
                    "player_name": player.name,
                    "leg_wins": player.leg_wins,
                    "message": f"{player.name} has won leg {game.current_leg - 1} of set {game.current_set}.",
                }
            )

        # non-finishing valid throw: subtract score and persist
        player.current_score = new_score
        db.session.add(t)
        db.session.add(player)
        db.session.commit()
        return jsonify({"status": "ok", "current_score": player.current_score})

    # Fallback: non-X01 mode (Cricket, training, etc.) - just record the throw by default
    db.session.add(t)
    db.session.commit()
    return jsonify({"status": "ok", "current_score": player.current_score})


if __name__ == "__main__":
    app.run(debug=True)
