# Hypothèses — statut

Rien n'est marqué « vérifié » sans retour du pilote (procédures dans `RUN_ON_PC.md`).

## A. Variables de télémétrie iRacing (toutes : **à vérifier** via `simengineer dump-vars`)
Tous les noms sont centralisés dans `src/simengineer/core/vars.py` (`VARS`).

| Hypothèse | Statut |
|---|---|
| `SessionTime`, `SessionTimeRemain`, `SessionLapsRemainEx`, `SessionFlags`, `SessionState` existent | à vérifier |
| `Lap`, `LapDistPct`, `LapDist`, `LapCurrentLapTime`, `LapLastLapTime` | à vérifier |
| `Speed` (m/s), `Throttle`/`Brake` (0..1), `SteeringWheelAngle` (rad), `Gear`, `RPM` | à vérifier |
| `FuelLevel` (litres), `OnPitRoad`, `IsOnTrack`, `PlayerTrackSurface`, `PlayerCarIdx` | à vérifier |
| `PlayerCarMyIncidentCount` augmente à chaque incident | à vérifier |
| `LatAccel`, `LongAccel` (m/s²), `YawRate` (rad/s) ; signes non supposés (estimés par corrélation) | à vérifier |
| `Lat`, `Lon` présents en direct (et pas seulement dans les .ibt) ; précision de trajectoire | à vérifier / à mesurer |
| `CarLeftRight` : 0 off, 1 clear, 2 gauche, 3 droite, 4 des deux côtés, 5 deux à gauche, 6 deux à droite | à vérifier |
| `CarIdxLapDistPct` (-1 si absente), `CarIdxTrackSurface`, `CarIdxOnPitRoad`, `CarIdxPosition` | à vérifier |
| `CarIdxF2Time` = écart au leader en secondes (classement), utilisé pour les écarts officiels | à vérifier |
| `CarIdxPosition` vaut 0 avant le départ / hors classement | à vérifier |
| `irsdk_TrkLoc` : -1 hors monde, 0 hors-piste, 1 stand, 2 approche stands, 3 en piste | à vérifier |
| Bits de `SessionFlags` (damier 0x1, blanc 0x2, vert 0x4, jaune 0x8, rouge 0x10, bleu 0x20, débris 0x40, jaune agité 0x100, caution 0x4000, caution agitée 0x8000, noir 0x10000, réparation 0x100000) | à vérifier |
| `SessionLapsRemainEx` = tours restants en comptant le tour en cours ; 32767 = illimité | à vérifier |
| `TrackTempCrew`, `AirTemp`, `TrackWetness`, `Precipitation` | à vérifier |
| Pneus `LFtempCL/CM/CR`, `LFwearL/M/R`, `LFpressure` (et RF/LR/RR) ; rafraîchis seulement aux stands | à vérifier |
| Ordre gauche/milieu/droite des températures : L = bord gauche du pneu vu de l'arrière (donc intérieur pour un pneu droit) | à vérifier |

## B. Infos de session (YAML)
| Hypothèse | Statut |
|---|---|
| `WeekendInfo.WeekendOptions.IncidentLimit` (nombre ou « unlimited ») | à vérifier |
| `WeekendInfo.TrackPitSpeedLimit` (« 60.00 kph », parfois en mph) | à vérifier |
| `WeekendInfo.TrackDisplayName`, `WeekendInfo.TrackLength` (« 3.70 km ») | à vérifier |
| `DriverInfo.DriverCarIdx`, `DriverInfo.DriverCarFuelMaxLtr`, `Drivers[].CarScreenName` | à vérifier |
| `SessionInfo.Sessions[].SessionType`, `SessionLaps` (« unlimited »), `SessionTime` (« 1800.0000 sec ») | à vérifier |
| `DriverCarFuelMaxLtr` doit être multiplié par `DriverCarMaxFuelPct` (non fait) | à vérifier |

## C. SDK / pyirsdk
| Hypothèse | Statut |
|---|---|
| `irsdk.IRSDK().startup()`, `is_initialized`, `is_connected`, `freeze_var_buffer_latest()`, `ir[nom]`, `var_headers_names` | à vérifier |
| `ir.pit_command(irsdk.PitCommandMode.fuel, litres)`, `.lf/.rf/.lr/.rr`, `.clear_tires` | à vérifier |
| La commande carburant du stand prend des litres entiers | à vérifier |
| Format `.ibt` : en-tête 112 octets, sous-en-tête disque 32 octets, en-têtes de variables 144 octets, YAML en cp1252 | à vérifier sur un vrai fichier |

## D. Fournisseurs LLM
| Hypothèse | Statut |
|---|---|
| Qwen international : `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` (vu dans la doc Alibaba Cloud Model Studio, région Singapour) | doc consultée, à confirmer avec la clé du pilote (région de la clé) |
| Identifiants `qwen-flash` (dialogue) et `qwen-plus` (rapports) | à vérifier dans la console Model Studio |
| DeepSeek : `https://api.deepseek.com`, modèle `deepseek-v4-flash` (page tarifs officielle) | à vérifier |
| Tarifs par million de jetons en configuration (`price_in_per_mtok`, `price_out_per_mtok`) | à vérifier, valeurs indicatives |
| Les deux fournisseurs acceptent `tools` au format OpenAI et renvoient `usage` | à vérifier |

## E. Voix
| Hypothèse | Statut |
|---|---|
| Voix Piper `fr_FR-siwis-medium` et `en_US-lessac-medium` (fichiers .onnx + .json à télécharger) | à vérifier |
| API Python `piper.PiperVoice.load(...)` / `synthesize_wav` (sinon exécutable `piper`) | à vérifier |
| faster-whisper `small` int8 transcrit une question de 5 s en < 2 s sur le CPU du pilote | à mesurer |
| Latence donnée → début de son < 200 ms avec phrases en cache | à mesurer |

## F. Modèles physiques
| Hypothèse | Statut |
|---|---|
| Course au temps : tours restants = ceil(temps restant / temps au tour moyen) + 1 (tour en cours) ; l'arrivée dépend en réalité du leader | approximation assumée |
| Indice de sous-virage : rapport direction/démultiplication 12:1 et empattement 2,46 m (Cup 992), configurables | à vérifier |
| Usure pneus linéaire par tour entre deux arrêts | approximation assumée |
| Débit de carburant ∝ (8 % ralenti + 92 % × accélérateur) le long du tour, étalonné sur la conso mesurée ; roue libre ≈ 1,6 m/s² | à vérifier sur la Cup/GT3 (économie annoncée vs réelle) |
| L'économie estimée d'un lever de pied est réaliste à ±35 % ; le plan est renforcé d'après l'économie réellement mesurée | à vérifier |

## G. Performance et packaging
| Hypothèse | Statut |
|---|---|
| Budget CPU/RAM tenu avec la source `live` (mesuré seulement en simulé : 1,74 %, 80 Mo) | à mesurer (`simengineer perf --source live`) |
| Pas de baisse de FPS iRacing | à mesurer |
| Rendu de l'interface dans Edge/Chrome sous Windows (police Bahnschrift) identique aux captures Chromium Linux | à vérifier |
| L'interface ouverte sur le PC de jeu ne fait pas baisser les FPS | à mesurer |
| L'écart en temps calculé depuis `CarIdxLapDistPct` suffit à détecter une voiture plus rapide sans variable de classe | à vérifier en course multiclasse |
| faster-whisper transcrit correctement « oui » / « non » courts pour la confirmation de stand | à vérifier |
| L'exécutable PyInstaller embarque correctement faster-whisper / Piper / onnxruntime | à vérifier |
