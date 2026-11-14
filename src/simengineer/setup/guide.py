"""Pistes de réglage et guide d'application MANUELLE dans le garage iRacing.

Les setups iRacing sont chiffrés : l'application ne lit ni n'écrit aucun fichier de setup.
Les pistes sont génériques (Cup / GT3) et classées de la moins à la plus intrusive.
"""

from __future__ import annotations

from simengineer.setup.diagnosis import Diagnosis, Finding
from simengineer.voice.templates import fmt_num

_PHASE = {
    "fr": {"entry": "en entrée", "mid": "à mi-virage", "exit": "en sortie"},
    "en": {"entry": "on entry", "mid": "mid-corner", "exit": "on exit"},
}
_TYRE = {
    "fr": {"lf": "avant gauche", "rf": "avant droit", "lr": "arrière gauche",
           "rr": "arrière droit", "front": "train avant", "rear": "train arrière"},
    "en": {"lf": "left front", "rf": "right front", "lr": "left rear", "rr": "right rear",
           "front": "front axle", "rear": "rear axle"},
}  # fmt: skip

# (problème, phase) -> (pistes FR, pistes EN)
_CHANGES: dict[tuple[str, str], tuple[list[str], list[str]]] = {
    ("understeer", "entry"): (
        [
            "Répartition de freinage : 0,5 % vers l'arrière.",
            "Barre antiroulis avant : un cran plus souple.",
            "Pilotage : relâche le frein plus progressivement pour garder du poids sur l'avant.",
        ],
        [
            "Brake bias: 0.5% rearward.",
            "Front anti-roll bar: one step softer.",
            "Driving: trail the brake off more gradually to keep weight on the front.",
        ],
    ),
    ("understeer", "mid"): (
        [
            "Barre antiroulis avant : un cran plus souple (ou arrière un cran plus dur).",
            "Carrossage avant : 0,2° de négatif en plus.",
            "Hauteur de caisse avant : 1 à 2 mm plus bas (plus d'assiette).",
        ],
        [
            "Front anti-roll bar: one step softer (or rear one step stiffer).",
            "Front camber: 0.2° more negative.",
            "Front ride height: 1-2 mm lower (more rake).",
        ],
    ),
    ("understeer", "exit"): (
        [
            "Barre antiroulis arrière : un cran plus dur.",
            "Aileron arrière : un cran de moins (si la stabilité rapide le permet).",
            "Pilotage : attends d'avoir débraqué avant de remettre plein gaz.",
        ],
        [
            "Rear anti-roll bar: one step stiffer.",
            "Rear wing: one step less (if high-speed stability allows).",
            "Driving: unwind the wheel before going to full throttle.",
        ],
    ),
    ("oversteer", "entry"): (
        [
            "Répartition de freinage : 0,5 % vers l'avant.",
            "Barre antiroulis arrière : un cran plus souple.",
            "Pilotage : moins de frein en braquant (dégressif plus tôt).",
        ],
        [
            "Brake bias: 0.5% forward.",
            "Rear anti-roll bar: one step softer.",
            "Driving: less brake while turning in (start trailing earlier).",
        ],
    ),
    ("oversteer", "mid"): (
        [
            "Barre antiroulis arrière : un cran plus souple (ou avant un cran plus dur).",
            "Aileron arrière : un cran de plus.",
            "Pressions arrière : vérifie qu'elles ne sont pas trop hautes à chaud.",
        ],
        [
            "Rear anti-roll bar: one step softer (or front one step stiffer).",
            "Rear wing: one step more.",
            "Rear pressures: check they are not too high when hot.",
        ],
    ),
    ("oversteer", "exit"): (
        [
            "Antipatinage : un cran de plus si la voiture en a un.",
            "Barre antiroulis arrière : un cran plus souple.",
            "Aileron arrière : un cran de plus ; pilotage : gaz plus progressifs.",
        ],
        [
            "Traction control: one step more if available.",
            "Rear anti-roll bar: one step softer.",
            "Rear wing: one step more; driving: more progressive throttle.",
        ],
    ),
}
_TYRE_CHANGES: dict[str, tuple[str, str]] = {
    "camber_high": ("réduis le carrossage négatif de 0,2°", "reduce negative camber by 0.2°"),
    "camber_low": ("ajoute 0,2° de carrossage négatif", "add 0.2° of negative camber"),
    "pressure_high": ("baisse la pression à froid de 3 à 5 kPa", "lower cold pressure by 3-5 kPa"),
    "pressure_low": ("monte la pression à froid de 3 à 5 kPa", "raise cold pressure by 3-5 kPa"),
    "axle_hot": (
        "ce train surchauffe : assouplis sa barre antiroulis ou soulage-le au pilotage",
        "this axle overheats: soften its anti-roll bar or ease the load on it",
    ),
}
_PROBLEM = {
    "fr": {"understeer": "Sous-virage", "oversteer": "Survirage",
           "camber_high": "Trop de carrossage",
           "camber_low": "Pas assez de carrossage", "pressure_high": "Pression trop haute",
           "pressure_low": "Pression trop basse", "axle_hot": "Surchauffe"},
    "en": {"understeer": "Understeer", "oversteer": "Oversteer", "camber_high": "Too much camber",
           "camber_low": "Not enough camber", "pressure_high": "Pressure too high",
           "pressure_low": "Pressure too low", "axle_hot": "Overheating"},
}  # fmt: skip


def describe(f: Finding, lang: str = "fr") -> str:
    lg = "fr" if lang == "fr" else "en"
    name = _PROBLEM[lg][f.problem]
    if f.problem == "understeer":
        return f"{name} {_PHASE[lg][f.where]} (indice {fmt_num(f.value, 2, lang)})"
    if f.problem == "oversteer":
        unit = "contre-braquages par tour" if lg == "fr" else "counter-steers per lap"
        return f"{name} {_PHASE[lg][f.where]} ({fmt_num(f.value, 1, lang)} {unit})"
    return f"{name} — {_TYRE[lg][f.where]} ({fmt_num(f.value, 1, lang)} °C)"


def suggestions(f: Finding, lang: str = "fr") -> list[str]:
    i = 0 if lang == "fr" else 1
    if (f.problem, f.where) in _CHANGES:
        return list(_CHANGES[(f.problem, f.where)][i])
    if f.problem in _TYRE_CHANGES:
        lg = "fr" if lang == "fr" else "en"
        return [f"{_TYRE[lg][f.where].capitalize()} : {_TYRE_CHANGES[f.problem][i]}."]
    return []


APPLY_GUIDE_FR = """## Appliquer un réglage à la main (les setups iRacing sont chiffrés)

1. Roule 5 tours propres avec le setup actuel.
2. Reviens au garage iRacing (onglet Garage) et change UN SEUL réglage, de la valeur indiquée.
3. Clique sur « Apply », puis « Save As » sous un nouveau nom (ex. `cup_arb_av_-1`).
4. Dans un terminal : `simengineer setup mark "ARB avant -1 cran"`.
5. Roule 5 tours propres, puis `simengineer setup compare`.
6. Garde le réglage si le verdict est « mieux », sinon recharge le setup précédent.

Un seul changement à la fois : sinon l'avant/après ne dit pas ce qui a marché.
"""

APPLY_GUIDE_EN = """## Applying a change by hand (iRacing setups are encrypted)

1. Drive 5 clean laps on the current setup.
2. Go back to the iRacing garage (Garage tab) and change ONE setting by the suggested amount.
3. Click "Apply", then "Save As" under a new name (e.g. `cup_farb_-1`).
4. In a terminal: `simengineer setup mark "front ARB -1 step"`.
5. Drive 5 clean laps, then run `simengineer setup compare`.
6. Keep the change if the verdict is "better", otherwise reload the previous setup.

One change at a time: otherwise the before/after cannot tell what worked.
"""


def apply_guide(lang: str = "fr") -> str:
    return APPLY_GUIDE_FR if lang == "fr" else APPLY_GUIDE_EN


def render_diagnosis(d: Diagnosis, lang: str = "fr") -> str:
    fr = lang == "fr"
    lg = "fr" if fr else "en"
    lines = [
        f"# {'Diagnostic de setup' if fr else 'Setup diagnosis'}"
        f" ({d.laps} {'tours propres' if fr else 'clean laps'})",
        "",
    ]
    if d.balance:
        lines += [
            "| Phase | Indice de sous-virage | Contre-braquages / tour |"
            if fr
            else "| Phase | Understeer index | Counter-steers / lap |",
            "|---|---|---|",
        ]
        for phase, b in d.balance.items():
            idx = fmt_num(b.understeer_index, 2, lang) if b.understeer_index is not None else "-"
            events = fmt_num(b.oversteer_events, 1, lang)
            lines.append(f"| {_PHASE[lg][phase]} | {idx} | {events} |")
        lines.append("")
    if not d.findings:
        lines.append("Aucun déséquilibre net détecté." if fr else "No clear imbalance detected.")
    for f in d.findings:
        lines += [f"## {describe(f, lang)}", ""]
        lines += [f"- {s}" for s in suggestions(f, lang)]
        lines.append("")
    lines.append(apply_guide(lang))
    return "\n".join(lines)
