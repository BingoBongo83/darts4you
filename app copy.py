import os
from datetime import datetime

from flask import Flask, jsonify, render_template, request
from flask_sqlalchemy import SQLAlchemy

from checkout import find_checkout

app = Flask(__name__, static_folder="static", template_folder="templates")
base_dir = os.path.abspath(os.path.dirname(__file__))
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(base_dir, "darts.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


# Models
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
    game_id = db.Column(db.Integer, db.ForeignKey("game.id"))
    throws = db.relationship("Throw", backref="player", cascade="all, delete-orphan")


class Throw(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey("player.id"))
    value = db.Column(db.Integer)
    multiplier = db.Column(db.Integer)  # 1 single, 2 double, 3 triple
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


# Initialize DB: create tables inside an application context
with app.app_context():
    db.create_all()


# Routes
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/new_game", methods=["POST"])
def new_game():
    payload = request.json or {}
    mode = payload.get("mode", "501")
    players = payload.get("players", ["Player 1", "Player 2"])
    starting = 501 if mode in ("501", "301") else 0
    if mode == "301":
        starting = 301
    game = Game(mode=mode)
    db.session.add(game)
    db.session.flush()  # get game.id
    for p in players:
        pl = Player(name=p, starting_score=starting, current_score=starting, game_id=game.id)
        db.session.add(pl)
    db.session.commit()
    return jsonify({"game_id": game.id})


@app.route("/api/game_state/<int:game_id>", methods=["GET"])
def game_state(game_id):
    game = Game.query.get_or_404(game_id)
    players = []
    for p in game.players:
        throws = Throw.query.filter_by(player_id=p.id).order_by(Throw.timestamp).all()
        total_scored = sum(t.value * t.multiplier for t in throws)
        throw_count = len(throws)
        avg_per_throw = (total_scored / throw_count) if throw_count else 0
        avg_3dart = avg_per_throw * 3
        # first-9-average (first 9 throws -> average per 3 darts)
        first9 = throws[:9]
        sum_first9 = sum(t.value * t.multiplier for t in first9)
        n_first9 = len(first9)
        first9_avg_3dart = (sum_first9 / n_first9 * 3) if n_first9 else 0
        last_score = (throws[-1].value * throws[-1].multiplier) if throws else 0
        suggestion = None
        if p.current_score <= 170 and p.current_score > 0 and game.mode in ("501", "301"):
            suggestion = find_checkout(p.current_score)
        players.append(
            {
                "id": p.id,
                "name": p.name,
                "current_score": p.current_score,
                "starting_score": p.starting_score,
                "total_scored": total_scored,
                "throw_count": throw_count,
                "avg_per_throw": round(avg_per_throw, 2),
                "avg_3dart": round(avg_3dart, 1),
                "first9_avg_3dart": round(first9_avg_3dart, 1),
                "last_score": last_score,
                "suggestion": suggestion,
            }
        )
    return jsonify({"game": {"id": game.id, "mode": game.mode}, "players": players})


@app.route("/api/throw", methods=["POST"])
def register_throw():
    data = request.json or {}
    player_id = data["player_id"]
    value = int(data["value"])
    multiplier = int(data["multiplier"])
    player = Player.query.get_or_404(player_id)
    scored = value * multiplier
    new_score = player.current_score - scored
    if player.game.mode in ("501", "301"):
        if new_score < 0 or new_score == 1:
            # bust: record throw but revert score to previous
            t = Throw(player_id=player.id, value=value, multiplier=multiplier)
            db.session.add(t)
            db.session.commit()
            return jsonify({"status": "bust", "current_score": player.current_score})
        # If exactly 0, must finish on a double
        if new_score == 0:
            if multiplier != 2 and scored != 50:  # 50 (bull) also valid as finishing double
                t = Throw(player_id=player.id, value=value, multiplier=multiplier)
                db.session.add(t)
                db.session.commit()
                return jsonify({"status": "invalid_finish_needs_double", "current_score": player.current_score})
            else:
                t = Throw(player_id=player.id, value=value, multiplier=multiplier)
                player.current_score = new_score
                db.session.add(t)
                db.session.commit()
                return jsonify({"status": "winner", "current_score": player.current_score})
        # normal valid throw
        t = Throw(player_id=player.id, value=value, multiplier=multiplier)
        player.current_score = new_score
        db.session.add(t)
        db.session.commit()
        # suggestion will be provided by client via /api/game_state refresh
        return jsonify({"status": "ok", "current_score": player.current_score})
    else:
        # For other modes, store throw but no score logic yet
        t = Throw(player_id=player.id, value=value, multiplier=multiplier)
        db.session.add(t)
        db.session.commit()
        return jsonify({"status": "ok", "current_score": player.current_score})


if __name__ == "__main__":
    app.run(debug=True)
