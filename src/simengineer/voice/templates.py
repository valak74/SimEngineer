"""Gabarits de phrases FR/EN. Tout ce que dit l'application sans LLM passe par ici.

Contrainte : les gabarits P0 (danger) et P2 (coaching) font au plus 8 mots (testé).
"""

from __future__ import annotations

from typing import Any

MAX_WORDS_SHORT = 8

# clé -> (fr, en)
TEMPLATES: dict[str, tuple[str, str]] = {
    # --- P0 spotter
    "car_left": ("Voiture à gauche", "Car left"),
    "car_right": ("Voiture à droite", "Car right"),
    "cars_both": ("Voitures des deux côtés", "Three wide, you're in the middle"),
    "two_left": ("Deux voitures à gauche", "Two cars left"),
    "two_right": ("Deux voitures à droite", "Two cars right"),
    "still_left": ("Toujours à gauche", "Still there, left"),
    "still_right": ("Toujours à droite", "Still there, right"),
    "clear": ("Libre", "Clear"),
    "stopped_ahead": ("Voiture arrêtée devant, {dist} mètres", "Stopped car ahead, {dist} meters"),
    "offtrack_ahead": ("Voiture hors piste devant, {dist} mètres", "Car off ahead, {dist} meters"),
    "accident_ahead": ("Accident devant, {dist} mètres", "Crash ahead, {dist} meters"),
    "flag_yellow": ("Drapeau jaune", "Yellow flag"),
    "flag_fcy": ("Jaune sur tout le circuit", "Full course yellow"),
    "flag_red": ("Drapeau rouge", "Red flag"),
    "flag_debris": ("Débris en piste", "Debris on track"),
    "flag_black": ("Drapeau noir pour toi", "Black flag for you"),
    "faster_behind": (
        "Voiture plus rapide, {s} secondes derrière",
        "Faster car, {s} seconds behind",
    ),
    "faster_behind_1": (
        "Voiture plus rapide, une seconde derrière",
        "Faster car, one second behind",
    ),
    "offtrack_self": ("Hors piste, attention au retour", "Off track, rejoin safely"),
    # --- P1 course
    "flag_green": ("Drapeau vert", "Green flag"),
    "flag_blue": ("Drapeau bleu", "Blue flag"),
    "flag_white": ("Dernier tour", "Last lap"),
    "flag_checkered": ("Drapeau à damier", "Checkered flag"),
    "fuel_status": (
        "Carburant pour {laps} tours, il en reste {remaining}",
        "Fuel for {laps} laps, {remaining} to go",
    ),
    "fuel_laps": ("Carburant pour {laps} tours", "Fuel for {laps} laps"),
    "fuel_low": ("Carburant bas, {laps} tours restants", "Low fuel, {laps} laps left"),
    "fuel_ok": (
        "Carburant suffisant pour finir, marge {margin} tours",
        "Fuel is good to the end, {margin} laps margin",
    ),
    "fuel_short": (
        "Il manque {liters} litres pour finir",
        "You are {liters} liters short to finish",
    ),
    "fuel_unknown": (
        "Pas encore assez de tours propres pour le carburant",
        "Not enough clean laps for fuel numbers yet",
    ),
    "fuel_save_plan": (
        "Économie de carburant : {save} litre par tour. Lève le pied {lifts}",
        "Fuel saving: {save} liters per lap. Lift {lifts}",
    ),
    "fuel_save_ok": ("Conso {used}, objectif tenu", "Used {used}, on target"),
    "fuel_save_more": (
        "Conso {used}, objectif {target} : lève un peu plus",
        "Used {used}, target {target}: lift a bit more",
    ),
    "fuel_save_abort": (
        "Économie insuffisante, prévois un arrêt : {liters} litres",
        "Saving won't be enough, plan a stop: {liters} liters",
    ),
    "fuel_save_status": (
        "Mode économie : objectif {target} litre par tour, lever {lifts}",
        "Fuel saving: target {target} liters per lap, lift {lifts}",
    ),
    "fuel_save_done": (
        "Carburant suffisant jusqu'à l'arrivée, tu peux attaquer",
        "Fuel is good to the end, you can push",
    ),
    # --- course : incidents, stands, position, écarts, briefing, débrief
    "incident": ("Incident : {total} sur {limit}", "Incident: {total} of {limit}"),
    "incident_nolimit": ("Incident : {total} au total", "Incident: {total} total"),
    "incident_warning": (
        "Attention, plus que {left} points avant la disqualification",
        "Careful, {left} points left before disqualification",
    ),
    "pit_lane_limit": ("Voie des stands, limite {kmh}", "Pit lane, limit {kmh}"),
    "pit_speeding": ("Vitesse dans les stands !", "Pit lane speeding!"),
    "position_gain": ("P{pos}, tu gagnes {n} place{s}", "P{pos}, up {n} place{s}"),
    "position_loss": ("P{pos}, tu perds {n} place{s}", "P{pos}, down {n} place{s}"),
    "gap_closing_ahead": (
        "Devant à {gap}, tu reprends {rate} par tour",
        "Car ahead {gap}, you gain {rate} a lap",
    ),
    "gap_closing_behind": (
        "Derrière à {gap}, il reprend {rate} par tour",
        "Car behind {gap}, he gains {rate} a lap",
    ),
    "laps_to_go": ("Plus que {laps} tours", "{laps} laps to go"),
    "minutes_to_go": ("Plus que {min} minutes", "{min} minutes to go"),
    "briefing_laps": ("Course de {laps} tours.", "{laps} lap race."),
    "briefing_time": (
        "Course de {min} minutes, environ {laps} tours.",
        "{min} minute race, about {laps} laps.",
    ),
    "briefing_fuel": (
        "Il faut environ {need} litres, tu en as {have} : {verdict}.",
        "You need about {need} liters, you have {have}: {verdict}.",
    ),
    "briefing_nofuel": (
        "Pas encore de conso connue sur ce circuit.",
        "No fuel data on this track yet.",
    ),
    "briefing_incidents": ("Limite d'incidents {limit}.", "Incident limit {limit}."),
    "verdict_enough": ("assez pour finir", "enough to finish"),
    "verdict_save": ("il faudra économiser un peu", "you will need to save a little"),
    "verdict_stop": ("un arrêt sera nécessaire", "you will need to stop"),
    "debrief": (
        "Course terminée, P{pos}{gain}. Meilleur tour {best}. {inc} points d'incident.",
        "Race over, P{pos}{gain}. Best lap {best}. {inc} incident points.",
    ),
    "debrief_gain": (", {n} place{s} gagnée{s}", ", up {n} place{s}"),
    "debrief_loss": (", {n} place{s} perdue{s}", ", down {n} place{s}"),
    # --- qualification
    "quali_outlap": (
        "Tour de lancement : chauffe pneus et freins",
        "Out lap: warm up tyres and brakes",
    ),
    "quali_flying": ("Tour lancé", "Flying lap"),
    "quali_sector": ("Secteur {n}, {delta}", "Sector {n}, {delta}"),
    "quali_sector_even": ("Secteur {n}, dans le temps", "Sector {n}, on the pace"),
    "quali_cooldown": ("Tour de décélération, garde du rythme", "Cool-down lap"),
    "pit_window_open": (
        "Fenêtre de stand ouverte, ajoute {liters} litres",
        "Pit window open, add {liters} liters",
    ),
    "pit_window_in": ("Fenêtre de stand dans {laps} tours", "Pit window opens in {laps} laps"),
    "pit_last_chance": ("Stand ce tour, carburant critique", "Box this lap, fuel critical"),
    "pit_now_yellow": (
        "Jaune : stand maintenant conseillé, ajoute {liters} litres",
        "Yellow: box now advised, add {liters} liters",
    ),
    "pit_no_stop": ("Pas d'arrêt nécessaire", "No stop needed"),
    "pit_status": (
        "Fenêtre de stand : tours {open} à {close}, {liters} litres à ajouter",
        "Pit window: laps {open} to {close}, add {liters} liters",
    ),
    "pit_cmd_disabled": (
        "Commandes de stand désactivées dans la configuration",
        "Pit commands are disabled in the configuration",
    ),
    "pit_confirm_fuel": (
        "{liters} litres au prochain arrêt, je valide ?",
        "{liters} liters at the next stop, confirm?",
    ),
    "pit_confirm_tyres_on": (
        "Changement des quatre pneus, je valide ?",
        "Change all four tyres, confirm?",
    ),
    "pit_confirm_tyres_off": (
        "Pas de pneus au prochain arrêt, je valide ?",
        "No tyres at the next stop, confirm?",
    ),
    "pit_cancelled": ("Annulé, rien n'est changé", "Cancelled, nothing changed"),
    "pit_nothing": ("Rien à valider", "Nothing to confirm"),
    "pit_cmd_fuel": ("Stand réglé : {liters} litres", "Pit set: {liters} liters"),
    "pit_cmd_tyres_on": ("Stand réglé : changement de pneus", "Pit set: tyre change"),
    "pit_cmd_tyres_off": ("Stand réglé : pas de pneus", "Pit set: no tyres"),
    "tyres_status": (
        "Pneus : {laps} tours, usure restante {wear} pour cent",
        "Tyres: {laps} laps, {wear} percent left",
    ),
    "tyres_stint": ("Pneus : {laps} tours dans le relais", "Tyres: {laps} laps this stint"),
    "tyres_worn": (
        "Pneus usés, {wear} pour cent restants",
        "Tyres worn, {wear} percent left",
    ),
    "weather_track_temp": (
        "Piste à {temp} degrés, {delta} depuis le départ",
        "Track at {temp} degrees, {delta} since the start",
    ),
    "weather_wet": ("La piste devient humide", "Track is getting wet"),
    "weather_drying": ("La piste sèche", "Track is drying"),
    "weather_status": ("Piste {temp} degrés, air {air} degrés", "Track {temp}, air {air} degrees"),
    "gap_status": (
        "Devant à {ahead} secondes, derrière à {behind} secondes",
        "Car ahead {ahead} seconds, behind {behind} seconds",
    ),
    "gap_ahead_only": ("Voiture devant à {ahead} secondes", "Car ahead at {ahead} seconds"),
    "gap_behind_only": ("Voiture derrière à {behind} secondes", "Car behind at {behind} seconds"),
    "gap_none": ("Personne à proximité", "Nobody close"),
    "lap_time": ("Tour en {time}", "Lap time {time}"),
    "lap_time_delta": ("Tour en {time}, {delta} sur ton meilleur", "Lap {time}, {delta} to best"),
    "lap_best": ("Meilleur tour, {time}", "Best lap, {time}"),
    "lap_summary": (
        "Dernier tour {time}, meilleur {best}",
        "Last lap {time}, best {best}",
    ),
    "lap_none": ("Pas encore de tour chronométré", "No timed lap yet"),
    # --- P2 coaching (≤ 8 mots)
    "adv_brake_later": (
        "{c}, freine {d} mètres plus tard",
        "{c}, brake {d} meters later",
    ),
    "adv_brake_earlier": (
        "{c}, freine {d} mètres plus tôt",
        "{c}, brake {d} meters earlier",
    ),
    "adv_brake_harder": (
        "{c}, freine plus fort à l'attaque",
        "{c}, hit the brake harder",
    ),
    "adv_brake_faster": (
        "{c}, monte en pression plus vite",
        "{c}, build brake pressure faster",
    ),
    "adv_trail_more": (
        "{c}, relâche le frein plus progressivement",
        "{c}, trail the brake off slower",
    ),
    "adv_min_speed": (
        "{c}, garde {kmh} km/h de plus",
        "{c}, carry {kmh} more km/h",
    ),
    "adv_throttle_earlier": (
        "{c}, gaz {d} mètres plus tôt",
        "{c}, throttle {d} meters earlier",
    ),
    "adv_exit_speed": ("{c}, soigne la sortie", "{c}, focus on the exit"),
    "adv_steering": (
        "{c}, moins de corrections au volant",
        "{c}, fewer steering corrections",
    ),
    "adv_none": ("Bon tour, continue comme ça", "Good lap, keep it up"),
    # Rappels du mode entraînement (≤ 8 mots), dits dans la ligne droite d'approche
    "call_braking": ("{c}, attaque franche au freinage", "{c}, firm brake hit"),
    "call_trail": ("{c}, relâche le frein progressivement", "{c}, trail off gently"),
    "call_min_speed": ("{c}, porte plus de vitesse", "{c}, carry more speed"),
    "call_throttle": ("{c}, gaz tôt et progressifs", "{c}, early smooth throttle"),
    "call_smoothness": ("{c}, un seul coup de volant", "{c}, one steering input"),
    "call_consistency": ("{c}, mêmes repères", "{c}, same markers"),
    "call_good": ("{c}, c'était bien, pareil", "{c}, good, same again"),
    "training_on": (
        "Mode entraînement : {theme}, virages {corners}",
        "Training mode: {theme}, turns {corners}",
    ),
    "trend_loss": (
        "{c}, {loss} seconde perdue depuis {laps} tours",
        "{c}, losing {loss} for {laps} laps",
    ),
    "delta_ref": ("{delta} sur la référence", "{delta} to reference"),
    # --- Version détaillée des conseils (rapports, écran)
    "advl_brake_later": (
        "{c} : freine ~{d} m plus tard (gain estimé {gain} s)",
        "{c}: brake ~{d} m later (est. gain {gain} s)",
    ),
    "advl_brake_earlier": (
        "{c} : freine ~{d} m plus tôt, tu sacrifies la sortie (gain estimé {gain} s)",
        "{c}: brake ~{d} m earlier, you are compromising the exit (est. gain {gain} s)",
    ),
    "advl_brake_harder": (
        "{c} : pression de frein maxi {pct} % plus basse que la référence (gain estimé {gain} s)",
        "{c}: peak brake pressure {pct}% lower than reference (est. gain {gain} s)",
    ),
    "advl_brake_faster": (
        "{c} : montée en pression trop lente, +{ms} ms (gain estimé {gain} s)",
        "{c}: brake pressure builds too slowly, +{ms} ms (est. gain {gain} s)",
    ),
    "advl_trail_more": (
        "{c} : relâche le frein plus progressivement jusqu'à la corde (gain estimé {gain} s)",
        "{c}: trail the brake off more gradually to the apex (est. gain {gain} s)",
    ),
    "advl_min_speed": (
        "{c} : garde ~{kmh} km/h de plus au point de corde (gain estimé {gain} s)",
        "{c}: carry ~{kmh} km/h more at the apex (est. gain {gain} s)",
    ),
    "advl_throttle_earlier": (
        "{c} : reprends les gaz ~{d} m plus tôt (gain estimé {gain} s)",
        "{c}: get back on throttle ~{d} m earlier (est. gain {gain} s)",
    ),
    "advl_exit_speed": (
        "{c} : vitesse de sortie {kmh} km/h plus basse (gain estimé {gain} s)",
        "{c}: exit speed {kmh} km/h lower (est. gain {gain} s)",
    ),
    "advl_steering": (
        "{c} : {count} corrections de volant de trop (gain estimé {gain} s)",
        "{c}: {count} extra steering corrections (est. gain {gain} s)",
    ),
    # --- P3 dialogue (secours par gabarit)
    "dlg_not_understood": (
        "Je n'ai pas compris, répète s'il te plaît",
        "I did not understand, please repeat",
    ),
    "dlg_no_advice": ("Pas de conseil pour l'instant", "No advice for now"),
    "dlg_no_data": ("Pas encore de données", "No data yet"),
}

_LANG = {"fr": 0, "en": 1}


def render(key: str, lang: str = "fr", **params: Any) -> str:
    fr_en = TEMPLATES[key]
    return fr_en[_LANG.get(lang, 0)].format(**params)


SPOKEN_LABEL_MAX_WORDS = 3


def corner_word(number: int, lang: str = "fr", label: str = "") -> str:
    """Nom prononcé d'un virage : son nom s'il est court (≤ 3 mots), sinon « Virage N »."""
    if label and len(label.split()) <= SPOKEN_LABEL_MAX_WORDS:
        return label
    return f"Virage {number}" if lang == "fr" else f"Turn {number}"


def word_count(text: str) -> int:
    return len(text.split())


def fmt_num(value: float, decimals: int = 1, lang: str = "fr") -> str:
    """Nombre lisible à voix haute : virgule décimale en français."""
    text = f"{value:.{decimals}f}"
    return text.replace(".", ",") if lang == "fr" else text


def fmt_lap_time(seconds: float, lang: str = "fr") -> str:
    """92.345 -> « 1:32,3 » (dixièmes, suffisant à l'oral)."""
    tenths = round(seconds * 10)
    minutes, rem = divmod(tenths, 600)
    sec = rem / 10
    text = f"{minutes}:{sec:04.1f}" if minutes else f"{sec:.1f}"
    return text.replace(".", ",") if lang == "fr" else text


def fmt_delta(seconds: float, lang: str = "fr") -> str:
    sign = "plus" if seconds >= 0 else "moins"
    if lang != "fr":
        sign = "plus" if seconds >= 0 else "minus"
    return f"{sign} {fmt_num(abs(seconds), 2, lang)}"


def static_phrases(lang: str = "fr") -> list[str]:
    """Phrases sans paramètre : pré-synthétisées dans le cache TTS au démarrage."""
    return [render(k, lang) for k, v in TEMPLATES.items() if "{" not in v[_LANG.get(lang, 0)]]


def danger_phrases(lang: str = "fr") -> list[str]:
    """Phrases P0 paramétrées, pré-synthétisées pour toutes les distances annoncées."""
    keys = ("stopped_ahead", "offtrack_ahead", "accident_ahead")
    out = [render(k, lang, dist=d) for k in keys for d in range(50, 651, 50)]
    return out + [render("faster_behind", lang, s=s) for s in range(2, 4)]
