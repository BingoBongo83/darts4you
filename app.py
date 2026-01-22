import os
from collections import defaultdict
from datetime import datetime

from flask import Flask, abort, jsonify, render_template, request
from flask_sqlalchemy import SQLAlchemy

from checkout import find_checkout

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


class Game(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    mode = db.Column(db.String(50), default="501")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    players = db.relationship("Player", backref="game", cascade="all, delete-orphan")


class Player(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), default="Player")
    starting_score = db.Column(db.Integer, default=501)
    current_score = db.Column(db.Integer, default=501)
    profile_id = db.Column(db.Integer, db.ForeignKey("profile.id"), nullable=True)
    profile = db.relationship("Profile", backref="game_players")
    game_id = db.Column(db.Integer, db.ForeignKey("game.id"))
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


# Ensure tables exist
with app.app_context():
    db.create_all()


# Helpers
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
        p = Profile(name=name)
        db.session.add(p)
        db.session.commit()
        return jsonify(profile_to_dict(p)), 201


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


# Game creation
@app.route("/api/new_game", methods=["POST"])
def new_game():
    payload = request.json or {}
    mode = payload.get("mode", "501")
    players_input = payload.get("players", [])  # expected list of profile ids OR names
    players_input = players_input[:6]
    starting = 501 if mode in ("501", "301") else 0
    if mode == "301":
        starting = 301
    game = Game(mode=mode)
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
    return jsonify({"game_id": game.id, "players_created": created_players})


# Game state (includes per-player last visit hits and stats)
@app.route("/api/game_state/<int:game_id>", methods=["GET"])
def game_state(game_id):
    game = Game.query.get_or_404(game_id)
    players_out = []
    for p in game.players:
        throws = Throw.query.filter_by(player_id=p.id).order_by(Throw.timestamp.desc()).limit(3).all()
        throws_chrono = list(reversed(throws))
        last_visit_score = sum(t.value * t.multiplier for t in throws_chrono) if throws_chrono else 0
        last_visit_hits = []
        for t in throws_chrono:
            hit = {
                "value": t.value,
                "multiplier": t.multiplier,
                "label": (
                    "BULL"
                    if (t.value == 25 and t.multiplier == 2)
                    else (
                        "SBULL"
                        if t.value == 25
                        else (("T" if t.multiplier == 3 else "D" if t.multiplier == 2 else "S") + str(t.value))
                    )
                ),
                "x": t.x,
                "y": t.y,
                "timestamp": t.timestamp.isoformat(),
            }
            last_visit_hits.append(hit)
        # compute stats
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
        suggestion = None
        if p.current_score <= 170 and p.current_score > 0 and game.mode in ("501", "301"):
            suggestion = find_checkout(p.current_score)
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
                "suggestion": suggestion,
            }
        )
    return jsonify({"game": {"id": game.id, "mode": game.mode}, "players": players_out})


# Throw API - accepts optional normalized x,y and records profile_id if player has one
@app.route("/api/throw", methods=["POST"])
def register_throw():
    data = request.json or {}
    player_id = data.get("player_id")
    if player_id is None:
        return jsonify({"error": "player_id required"}), 400
    value = int(data.get("value", 0))
    multiplier = int(data.get("multiplier", 0))
    x = data.get("x")
    y = data.get("y")
    player = Player.query.get_or_404(player_id)
    scored = value * multiplier
    new_score = player.current_score - scored
    t = Throw(player_id=player.id, value=value, multiplier=multiplier)
    if x is not None and y is not None:
        try:
            t.x = float(x)
            t.y = float(y)
        except:
            t.x = None
            t.y = None
    if player.profile_id:
        t.profile_id = player.profile_id
    if player.game.mode in ("501", "301"):
        if new_score < 0 or new_score == 1:
            db.session.add(t)
            db.session.commit()
            return jsonify({"status": "bust", "current_score": player.current_score})
        if new_score == 0:
            if multiplier != 2 and scored != 50:
                db.session.add(t)
                db.session.commit()
                return jsonify({"status": "invalid_finish_needs_double", "current_score": player.current_score})
            else:
                player.current_score = 0
                db.session.add(t)
                db.session.add(player)
                db.session.commit()
                return jsonify({"status": "winner", "current_score": player.current_score})
        player.current_score = new_score
        db.session.add(t)
        db.session.add(player)
        db.session.commit()
        return jsonify({"status": "ok", "current_score": player.current_score})
    else:
        db.session.add(t)
        db.session.commit()
        return jsonify({"status": "ok", "current_score": player.current_score})


if __name__ == "__main__":
    app.run(debug=True)
