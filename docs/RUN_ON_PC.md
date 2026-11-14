# À faire sur le PC de jeu (Windows 10/11)

Tout ce qui suit n'a pas pu être vérifié dans l'environnement de développement (Linux, sans
iRacing, sans carte audio, sans clé d'API). Le code est écrit et testé en simulé ; chaque point
ci-dessous doit être confirmé sur ton PC. **Renvoie-moi le résultat de chaque point** (une
ligne suffit, ou le fichier indiqué) : je corrigerai `VARS` et les hypothèses de
`docs/ASSUMPTIONS.md` en conséquence.

## A. Installation

1. Installe **Python 3.12** (python.org, case « Add to PATH » cochée) et git.
2. Clone le dépôt, puis dans PowerShell à la racine du dépôt :
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
   ```
   Cela crée `.venv`, installe `simengineer[live,llm,voice,perf]` et copie
   `simengineer.example.toml` en `simengineer.toml` (source = `live`).
3. **Voix Piper** : télécharge dans `models\` les fichiers `.onnx` **et** `.onnx.json` de
   `fr_FR-siwis-medium` (et `en_US-lessac-medium` si tu veux l'anglais) depuis le dépôt
   officiel des voix Piper (`rhasspy/piper-voices` sur Hugging Face). Les chemins sont dans
   `simengineer.toml` (`voice.piper_voice_fr`, `voice.piper_voice_en`).
4. **Clés d'API** (jamais dans le code ni dans git) — au choix :
   ```powershell
   setx DASHSCOPE_API_KEY "ta-clé-qwen"      # fournisseur principal (Alibaba Cloud Model Studio)
   setx DEEPSEEK_API_KEY  "ta-clé-deepseek"  # fournisseur de secours
   ```
   (rouvre le terminal ensuite), ou crée un fichier `.env` à la racine (ignoré par git) :
   ```
   DASHSCOPE_API_KEY=...
   DEEPSEEK_API_KEY=...
   ```
   Sans clé, l'application fonctionne en mode « tout local » (réponses par gabarits).

Dans la suite, `simengineer` = `.\.venv\Scripts\simengineer.exe`.

## B. Vérifications (liste numérotée)

| # | Commande / action | Résultat attendu | À me renvoyer |
|---|---|---|---|
| 1 | `simengineer run --source synthetic --no-realtime --null-voice` | une course de 12 tours défile en texte (`[P0] Voiture à droite`, carburant, conseils), fin par « Session N terminée » | OK / erreur |
| 2 | `simengineer devices` | liste des périphériques audio ; mets le nom (ou l'index) du casque dans `voice.output_device` et du micro dans `voice.input_device` | la liste |
| 3 | `simengineer say-test` | 4 phrases entendues dans le bon périphérique, dans l'ordre P0, P0, P1, P2 ; « latence donnée -> son » affichée | les latences (objectif < 200 ms pour les P0) |
| 4 | iRacing lancé, **en piste** dans une session de test : `simengineer dump-vars --source live` | `docs\irsdk_vars.md` écrit ; message « Toutes les variables de VARS sont présentes » ou liste des absentes | **le fichier `docs\irsdk_vars.md`** (indispensable) |
| 5 | En piste : `simengineer run --source live --null-voice` puis un tour | ligne de log « Session N : <circuit>, <voiture>, <type> » correcte ; « Meilleur tour, … » après le premier tour complet, proche du temps iRacing | circuit/voiture/type affichés, temps au tour vs iRacing |
| 6 | Enregistre une télémétrie disque dans iRacing (Alt+L), roule 3 tours, puis `simengineer dump-vars --source ibt_replay --ibt <fichier.ibt> --out docs\ibt_vars.md` et `simengineer analyze --ibt <fichier.ibt>` | pas d'erreur de format ; tours listés avec des temps corrects ; nombre de virages plausible | sortie des deux commandes (valide le lecteur `.ibt`) |
| 7 | Course contre l'IA, voix activée (`simengineer run --source live`) : laisse une IA se mettre à ta gauche, puis à ta droite | « Voiture à gauche » / « à droite » du bon côté, « Libre » quand elle est passée, pas de bégaiement | côtés corrects ? retards ? fausses alertes ? |
| 8 | Même session : une IA arrêtée ou en tête-à-queue devant toi | « Voiture arrêtée devant, N mètres » avec une distance plausible, rappel vers 150 m, **jamais de côté annoncé** | distance annoncée vs réalité ; `data\alerts.jsonl` |
| 9 | Drapeaux : jaune local, neutralisation, vert, bleu, dernier tour, damier | annonce correcte à chaque changement | drapeaux mal annoncés ou manqués |
| 10 | Sortie de piste volontaire à vitesse modérée | « Hors piste, attention au retour » ; le tour est marqué invalide dans `simengineer analyze` | OK / non |
| 11 | Course en tours contre l'IA, 6 tours propres : compare l'annonce « Carburant pour X tours, il en reste Y » avec la boîte noire carburant d'iRacing | X à ±0,3 tour ; Y = tours restants réels | X, Y annoncés vs iRacing (valide `SessionLapsRemainEx`) |
| 12 | Même test en **course au temps** | tours restants cohérents (±1) avec le déroulé réel | tours annoncés vs tours réellement courus |
| 13 | Neutralisation longue (> 20 s) avec arrêt nécessaire | « Jaune : stand maintenant conseillé, ajoute N litres » une seule fois | OK / non |
| 14 | Dans `simengineer.toml` : `pit_commands_enabled = true`. En piste : `simengineer ask "mets 20 litres" --local` n'est PAS suffisant (rejeu synthétique) ; utilise le push-to-talk en session réelle et dis « mets 20 litres », puis « pas de pneus » | la boîte noire de stand d'iRacing affiche 20 L, pneus décochés | OK / non (valide les commandes broadcast) |
| 15 | Après un arrêt au stand avec changement de pneus, demande « comment vont les pneus ? » | relais remis à zéro ; températures présentes | réponse entendue |
| 16 | Push-to-talk : règle `voice.ptt_kind` (`keyboard` + `ptt_key`, ou `joystick` + `ptt_joystick_index`/`ptt_button`), maintiens le bouton et demande « combien de carburant ? » | réponse vocale en moins de ~4 s, chiffres identiques à la boîte noire | délai ressenti ; transcription dans le log (« Question : … ») |
| 17 | Avec les clés d'API : `simengineer ask "combien de carburant ?"` | `[source : llm:qwen ; outils : ['get_fuel_status']]` | la ligne source ; sinon le message d'erreur du log (identifiant de modèle ou URL à corriger) |
| 18 | Bascule : `set DASHSCOPE_API_KEY=` (vide) dans le terminal puis même commande | `[source : llm:deepseek …]` | la ligne source |
| 19 | Sans aucune clé, ou `--local` | `[source : local …]`, réponse par gabarit | OK / non |
| 20 | 5 tours propres seul en piste, puis `simengineer analyze` | virages T01…Tn dans l'ordre du circuit, conseils chiffrés plausibles (« freine ~N m plus tard ») | nombre de virages détectés vs réel ; conseils absurdes éventuels |
| 21 | `simengineer measure-position` après ces 5 tours | dispersion médiane tour à tour et pas de quantification en mètres | les 5 lignes affichées (précision de trajectoire : **à mesurer**) |
| 22 | Importe un de tes tours : `simengineer ref import <fichier.ibt> --source "mon tour" --rights "personnel"`, puis roule | log « Référence : … » au démarrage ; conseils comparés à ce tour ; `simengineer ask "mon dernier tour ?"` donne le delta | OK / non |
| 23 | **Performance** en roulant : `simengineer perf --source live --seconds 120` | « Dans le budget : OUI » (CPU < 3 % d'un cœur, RAM < 150 Mo) | les 8 lignes affichées |
| 24 | **FPS** : note les FPS iRacing (compteur en jeu) sur un même replay, sans puis avec `simengineer run --source live` | écart nul ou négligeable | FPS sans / avec |
| 25 | CPU du dialogue : pendant une question vocale, regarde le gestionnaire des tâches | pic bref sur 2 cœurs max (faster-whisper), FPS stables | pic observé ; si gênant, on passera à une API de transcription |
| 26 | Démarrage automatique : `powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1`, redémarre Windows, lance iRacing | fenêtre SimEngineer réduite « En attente d'iRacing… » puis « Connecté à iRacing. » ; se remet en attente après la session | OK / non |
| 27 | (facultatif) Exécutable autonome : `scripts\build_exe.ps1` puis `dist\simengineer\simengineer.exe say-test` | même comportement que la version Python | OK / erreur PyInstaller |
| 28 | Setup : après un arrêt au stand, `simengineer setup diagnose` ; compare l'ordre gauche/milieu/droite des températures avec l'écran garage d'iRacing | températures identiques, intérieur/extérieur bien identifiés | ordre constaté |
| 29 | Setup avant/après : 5 tours, change un réglage, `simengineer setup mark "…"`, 5 tours, `simengineer setup compare` | tableau avant/après et verdict | sortie |
| 30 | Interface : `simengineer run --source live` ouvre `http://127.0.0.1:8765/` dans le navigateur | onglet Direct « En attente » puis « En direct » en piste ; delta, carburant, stand, radio, carte du circuit après le premier tour de référence | capture d'écran ; éléments faux ou illisibles |
| 31 | FPS iRacing avec l'interface ouverte sur le **même** PC, puis sur une tablette/téléphone (`ui.host = "0.0.0.0"` dans `simengineer.toml`, puis `http://<IP-du-PC>:8765/`, autoriser le port dans le pare-feu Windows) | pas de baisse de FPS notable ; mise à jour fluide | FPS sans / avec interface locale / avec tablette |
| 32 | `simengineer perf --source live --with-ui --seconds 120`, interface ouverte | « Dans le budget : OUI » | les lignes affichées |
| 33 | Mode entraînement : `simengineer run --source live --training`, 10 tours après au moins une séance analysée sur ce circuit | « Mode entraînement : <thème>, virages … » puis, à chaque tour, le rappel arrive dans la ligne droite AVANT le freinage, jamais en plein virage | rappels trop tôt / trop tard (`coach.callout_lead_s`, défaut 3,5 s) ; gênant ou utile ? |
| 34 | Onglet Séances, clique un tour puis un virage | traces tour/référence lisibles, zoom, réticule qui suit la souris (ou le doigt sur tablette) | capture ; courbes absurdes éventuelles (signe du volant, unités) |

## C. Variables d'environnement

| Variable | Rôle |
|---|---|
| `DASHSCOPE_API_KEY` | clé Qwen (Alibaba Cloud Model Studio, endpoint international) — fournisseur par défaut |
| `DEEPSEEK_API_KEY` | clé DeepSeek — fournisseur de secours |
| `SIMENGINEER__SECTION__CLE` | surcharge n'importe quel réglage, ex. `SIMENGINEER__LLM__ENABLED=false` (mode tout local), `SIMENGINEER__LLM__DAILY_BUDGET_USD=0.2`, `SIMENGINEER__SOURCE__KIND=live` |

Changer de fournisseur ou de modèle se fait uniquement dans `simengineer.toml`
(`[[llm.providers]]` : `base_url`, `model`, `report_model`, `api_key_env`, tarifs).

## D. Lancer l'outil

```powershell
simengineer run --source live            # attend iRacing, puis spotter + ingénieur + coach + dialogue + interface
simengineer run --source live --training # + rappels vocaux avant les virages du programme
simengineer ui                           # interface seule (historique : séances, pilote, setup, références)
simengineer run --source live --null-voice   # sans audio : affiche ce qui serait dit
simengineer analyze                      # conseils par virage de la dernière session
simengineer report [--llm]               # rapport Markdown (commentaire LLM optionnel)
simengineer profile ; simengineer training   # profil pilote et programme de la prochaine séance
simengineer ref import <fichier> --source "…" --rights "…"   # tour de référence
simengineer setup diagnose | mark "…" | compare | guide
simengineer perf --source live           # mesure CPU/RAM
```

Interface : `http://127.0.0.1:8765/` (ouverte automatiquement ; `--no-ui` pour la désactiver,
`[ui]` dans `simengineer.toml` pour le port, l'ouverture automatique et l'accès tablette).
Démo de l'interface sans iRacing :
`simengineer run --source synthetic --scenario race --speed 6 --null-voice --keep-ui`.

Sans iRacing : `--source synthetic` (scénarios `race`, `clean`, `stopped_car`, `overtake`,
`accident`, `perf`) ou `--source ibt_replay --ibt <fichier.ibt>`.
