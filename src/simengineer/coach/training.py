"""Programme d'entraînement : UN thème par séance, des exercices et un objectif mesurable."""

from __future__ import annotations

from dataclasses import dataclass, field

from simengineer.coach.advice import ISSUE_THEME
from simengineer.coach.profile import DriverProfile
from simengineer.coach.report import theme_label
from simengineer.voice.templates import fmt_num

# thème -> (exercices FR, exercices EN)
_DRILLS: dict[str, tuple[list[str], list[str]]] = {
    "braking": (
        [
            "10 tours en reculant ton repère de freinage de 5 m à la fois sur les virages ciblés.",
            "Attaque franche : pression maxi atteinte en moins de 0,2 s, puis relâche.",
            "Si tu rates la corde deux fois de suite, reviens au repère précédent.",
        ],
        [
            "10 laps moving your braking marker 5 m later at a time in the target corners.",
            "Firm initial hit: reach peak pressure within 0.2 s, then bleed off.",
            "If you miss the apex twice in a row, go back to the previous marker.",
        ],
    ),
    "trail": (
        [
            "Relâche le frein progressivement jusqu'à la corde : compte « 3-2-1 » en tournant.",
            "5 tours à 90 % en te concentrant uniquement sur la fin du freinage.",
            "Vérifie dans le rapport que la distance de relâchement augmente.",
        ],
        [
            "Bleed the brake off progressively down to the apex: count '3-2-1' while turning in.",
            "5 laps at 90% focusing only on the end of the braking phase.",
            "Check in the report that the release distance increases.",
        ],
    ),
    "min_speed": (
        [
            "Garde le même repère de freinage mais relâche plus tôt : vise +2 km/h à la corde.",
            "Regarde la sortie dès le point de braquage pour ouvrir ta trajectoire.",
            "8 tours en ne travaillant que les virages ciblés, le reste à 95 %.",
        ],
        [
            "Keep the same braking marker but release earlier: aim for +2 km/h at the apex.",
            "Look at the exit from turn-in to open up your line.",
            "8 laps working only the target corners, the rest at 95%.",
        ],
    ),
    "throttle": (
        [
            "Reprends les gaz dès la corde, progressivement, sans jamais relever ensuite.",
            "Si tu dois relever, c'est que tu as repris trop tôt ou trop fort : ajuste de 5 m.",
            "Compare ta vitesse de sortie au rapport après 8 tours.",
        ],
        [
            "Pick up the throttle at the apex, progressively, and never lift afterwards.",
            "If you have to lift, you picked up too early or too hard: adjust by 5 m.",
            "Compare your exit speed in the report after 8 laps.",
        ],
    ),
    "smoothness": (
        [
            "Un seul coup de volant par virage : braque une fois, tiens, débraque.",
            "5 tours à 90 % en cherchant zéro correction dans les virages ciblés.",
            "Mains souples : si la voiture bouge, corrige avec les pédales d'abord.",
        ],
        [
            "One steering input per corner: turn once, hold, unwind.",
            "5 laps at 90% aiming for zero corrections in the target corners.",
            "Soft hands: if the car moves, correct with the pedals first.",
        ],
    ),
    "consistency": (
        [
            "Relais de 10 tours à 97 % : objectif tous les tours dans la même demi-seconde.",
            "Mêmes repères à chaque tour ; ne cherche pas le temps.",
            "Un tour raté ne se rattrape pas : reprends ton rythme au tour suivant.",
        ],
        [
            "10-lap stint at 97%: every lap within the same half second.",
            "Same markers every lap; do not chase lap time.",
            "A bad lap cannot be recovered: get back to your rhythm on the next one.",
        ],
    ),
}


@dataclass(slots=True)
class TrainingPlan:
    theme: str
    title: str
    focus_corners: list[str] = field(default_factory=list)
    drills: list[str] = field(default_factory=list)
    target: str = ""
    rationale: str = ""

    def render(self, lang: str = "fr") -> str:
        fr = lang == "fr"
        lines = [f"# {'Programme de la séance' if fr else 'Session programme'} : {self.title}", ""]
        if self.rationale:
            lines += [self.rationale, ""]
        if self.focus_corners:
            lines += [
                f"**{'Virages ciblés' if fr else 'Target corners'}** : "
                + ", ".join(self.focus_corners),
                "",
            ]
        lines += [f"## {'Exercices' if fr else 'Drills'}", ""]
        lines += [f"{i}. {d}" for i, d in enumerate(self.drills, 1)]
        lines += ["", f"## {'Objectif mesurable' if fr else 'Measurable target'}", "", self.target]
        return "\n".join(lines) + "\n"


def build_plan(profile: DriverProfile, lang: str = "fr", avoid: str | None = None) -> TrainingPlan:
    """Choisit le thème le plus coûteux (en évitant `avoid` si un autre pèse presque autant)."""
    fr = lang == "fr"
    costs = sorted(profile.theme_cost_s.items(), key=lambda kv: -kv[1])
    if not costs:
        theme, cost = "consistency", profile.consistency_s or 0.0
    else:
        theme, cost = costs[0]
        if avoid == theme and len(costs) > 1 and costs[1][1] >= 0.7 * cost:
            theme, cost = costs[1]  # alterner quand deux thèmes se valent
    focus = [
        c
        for c in sorted(profile.corners, key=lambda c: -c.last_loss_s)
        if c.main_issue and ISSUE_THEME[c.main_issue] == theme and c.last_loss_s > 0
    ][:3]
    drills = _DRILLS[theme][0 if fr else 1]
    cost_txt = fmt_num(cost, 2, lang)
    if theme == "consistency":
        goal = fmt_num((profile.consistency_s or 0.0) / 2.0, 2, lang)
        target = (
            f"Écart-type des tours valides sous {goal} s (actuellement {cost_txt} s)."
            if fr
            else f"Valid lap std dev under {goal} s (currently {cost_txt} s)."
        )
        rationale = (
            f"Tes tours varient de {cost_txt} s en moyenne : la régularité d'abord."
            if fr
            else f"Your laps vary by {cost_txt} s on average: consistency first."
        )
    else:
        top = focus[0] if focus else None
        now = fmt_num(top.last_loss_s, 2, lang) if top else cost_txt
        goal = fmt_num((top.last_loss_s if top else cost) / 2.0, 2, lang)
        where = top.corner if top else ""
        target = (
            f"Perte moyenne au {where} : passer de {now} s à {goal} s par tour."
            if fr
            else f"Mean loss at {where}: from {now} s down to {goal} s per lap."
        )
        rationale = (
            f"Ce thème te coûte environ {cost_txt} s par tour sur ce circuit."
            if fr
            else f"This theme costs you about {cost_txt} s per lap on this track."
        )
    return TrainingPlan(
        theme=theme,
        title=theme_label(theme, lang),
        focus_corners=[c.corner for c in focus],
        drills=list(drills),
        target=target,
        rationale=rationale,
    )
