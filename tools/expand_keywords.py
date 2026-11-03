#!/usr/bin/env python3
"""Grow data/keywords.csv deterministically - the content reservoir.

Why this exists
---------------
The keyword pool is the *only* hard limit on how big the blog can get. With a
45-day keyword cooldown, a pool of N topics supports N/45 posts a day forever.
At 2,561 topics that ceiling is ~57 posts/day in theory but only ~51 days of
unique content in practice at a 50/day rate - after which the planner starts
recycling subjects and the site fills with near-duplicates.

So: make the pool big, make it *varied*, and make it honest. Every row here is
a real product a shopper searches for, written in one of the sentence shapes
the existing file already uses (so keywords.py parses it identically).

Design rules
------------
1. **Family-first.** Comparisons are only generated when a family holds two or
   more distinct product bases (see topic_planner._find_comparable_pair). Every
   family below therefore gets at least two products, which is what turns
   thousands of lone reviews into genuine "X vs Y" decision pages.
2. **Base <= 5 words.** topic_planner drops any keyword whose extracted base is
   longer, so long noun phrases are wasted rows.
3. **Deterministic.** A fixed seed means two runs produce byte-identical output.
4. **Never destructive.** Existing rows are preserved verbatim and the file
   keeps its CRLF endings; new rows are appended after de-duplication.

Usage
-----
    python3 tools/expand_keywords.py            # append, report counts
    python3 tools/expand_keywords.py --dry-run  # show what would be added
    python3 tools/expand_keywords.py --stats    # pool report only
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEYWORDS_CSV = ROOT / "data" / "keywords.csv"
HEADER = ["keyword", "niche", "feature", "audience", "asin", "priority"]
SEED = 20261103

# ---------------------------------------------------------------------------
# The catalogue.
#   niche -> list of (family_label, [products...], [features...], [audiences...])
# Families are deliberately pairs-or-better so comparisons become possible.
# ---------------------------------------------------------------------------
CATALOG: dict[str, list[tuple[str, list[str], list[str], list[str]]]] = {
    "kitchen": [
        ("air fryer", ["air fryer", "air fryer oven", "toaster oven"],
         ["basket capacity", "even heating", "cleaning ease"],
         ["small kitchens", "busy weeknights", "apartment dwellers"]),
        ("blender", ["blender", "immersion blender", "personal blender"],
         ["motor power", "blade quality", "cleaning ease"],
         ["smoothie drinkers", "small kitchens", "meal preppers"]),
        ("cookware", ["nonstick pan", "cast iron skillet", "stainless steel pan"],
         ["heat distribution", "coating durability", "oven safe limit"],
         ["beginner cooks", "induction cooktops", "daily use"]),
        ("knife", ["chef knife", "santoku knife", "bread knife"],
         ["blade quality", "edge retention", "handle balance"],
         ["home cooks", "beginner cooks", "small hands"]),
        ("coffee", ["coffee grinder", "espresso machine", "pour over kit"],
         ["consistent grind", "temperature stability", "cleaning ease"],
         ["coffee drinkers", "busy mornings", "small kitchens"]),
        ("food storage", ["food storage containers", "vacuum sealer", "mason jars"],
         ["airtight seal", "stackability", "dishwasher safe parts"],
         ["meal preppers", "bulk shoppers", "small kitchens"]),
        ("small appliances", ["rice cooker", "slow cooker", "pressure cooker"],
         ["capacity", "programmable timer", "cleaning ease"],
         ["families", "busy weeknights", "batch cooking"]),
        ("bakeware", ["cake pan", "baking sheet", "muffin tin"],
         ["even baking", "coating durability", "warp resistance"],
         ["home bakers", "beginner cooks", "holiday baking"]),
        ("utensils", ["spatula set", "tongs", "wooden spoon set"],
         ["heat resistance", "material safety", "handle comfort"],
         ["daily use", "nonstick cookware", "beginner cooks"]),
        ("measuring", ["kitchen scale", "measuring cup set", "food thermometer"],
         ["accuracy", "readability", "calibration"],
         ["bakers", "meal preppers", "precise cooks"]),
        ("water", ["electric kettle", "water filter pitcher", "instant hot water dispenser"],
         ["boil time", "filter life", "capacity"],
         ["tea drinkers", "small kitchens", "hard water areas"]),
        ("prep tools", ["vegetable chopper", "mandoline slicer", "box grater"],
         ["blade quality", "safety guard", "cleaning ease"],
         ["meal preppers", "busy weeknights", "beginner cooks"]),
    ],
    "fitness": [
        ("dumbbell", ["adjustable dumbbells", "fixed dumbbells", "kettlebell"],
         ["weight range", "grip texture", "space needed"],
         ["home gyms", "small apartments", "beginners"]),
        ("resistance", ["resistance bands set", "loop bands", "tube bands with handles"],
         ["tension range", "snap resistance", "portability"],
         ["travellers", "home gyms", "rehab users"]),
        ("yoga mat", ["yoga mat", "exercise mat", "travel yoga mat"],
         ["grip when wet", "thickness", "material safety"],
         ["hot yoga", "hardwood floors", "beginners"]),
        ("cardio", ["jump rope", "rowing machine", "exercise bike"],
         ["noise level", "resistance range", "foldability"],
         ["apartments", "home gyms", "joint friendly training"]),
        ("treadmill", ["under desk walking pad", "folding treadmill"],
         ["belt size", "motor power", "noise level"],
         ["apartments", "desk workers", "walkers"]),
        ("strength", ["pull up bar", "power rack", "weight bench"],
         ["weight capacity", "build rigidity", "doorway fit"],
         ["renters", "home gyms", "small spaces"]),
        ("recovery", ["foam roller", "massage gun", "massage ball"],
         ["firmness", "battery life", "noise level"],
         ["runners", "desk workers", "back pain sufferers"]),
        ("tracking", ["smart scale", "fitness tracker", "heart rate monitor"],
         ["accuracy", "app quality", "battery life"],
         ["data driven trainees", "beginners", "couples"]),
        ("gym bag", ["gym bag", "duffel bag with shoe compartment"],
         ["compartment layout", "strap comfort", "water resistance"],
         ["commuters", "swimmers", "frequent travellers"]),
        ("accessories", ["weight lifting gloves", "lifting straps", "weight belt"],
         ["grip support", "wrist support", "sizing accuracy"],
         ["beginners", "heavy lifters", "cross training"]),
    ],
    "electronics": [
        ("charger", ["gan charger", "usb c charger", "wireless charger"],
         ["charging speed", "port count", "heat control"],
         ["frequent travellers", "desk setups", "multi device households"]),
        ("power bank", ["power bank", "magsafe battery pack", "portable power station"],
         ["capacity", "charging speed", "airline size limits"],
         ["frequent travellers", "camping", "commuters"]),
        ("cable", ["usb c cable", "braided lightning cable", "hdmi cable"],
         ["durability", "data rate", "length options"],
         ["daily use", "desk setups", "home theatre"]),
        ("display", ["portable monitor", "4k monitor", "ultrawide monitor"],
         ["panel quality", "colour accuracy", "stand adjustability"],
         ["laptop users", "dual monitor setups", "photo editing"]),
        ("storage", ["external ssd", "sd card", "usb flash drive"],
         ["transfer speed", "durability", "capacity"],
         ["photographers", "students", "video editors"]),
        ("hub", ["usb c hub", "docking station", "card reader"],
         ["port selection", "power delivery", "heat control"],
         ["laptop users", "desk setups", "mac users"]),
        ("input", ["mechanical keyboard", "wireless mouse", "trackpad"],
         ["switch feel", "battery life", "latency"],
         ["typists", "desk workers", "quiet offices"]),
        ("webcam", ["webcam", "streaming camera"],
         ["low light performance", "autofocus speed", "mic quality"],
         ["video calls", "streamers", "home offices"]),
        ("protection", ["laptop sleeve", "surge protector", "screen protector"],
         ["padding", "outlet count", "clarity"],
         ["commuters", "home offices", "storm areas"]),
    ],
    "outdoor": [
        ("tent", ["camping tent", "backpacking tent", "pop up tent"],
         ["weatherproofing", "packed size", "setup time"],
         ["beginner campers", "backpackers", "families"]),
        ("sleeping", ["sleeping bag", "sleeping pad", "camping pillow"],
         ["temperature rating", "packed size", "warmth to weight"],
         ["cold sleepers", "backpackers", "car campers"]),
        ("cooking", ["camping stove", "portable grill", "camp cookware set"],
         ["fuel efficiency", "boil time", "packed size"],
         ["car campers", "backpackers", "weekend trips"]),
        ("hydration", ["insulated water bottle", "hydration bladder", "water filter"],
         ["cold retention", "leak proofing", "filter life"],
         ["hikers", "long shifts", "backpacking"]),
        ("backpack", ["hiking backpack", "daypack", "dry bag"],
         ["load comfort", "water resistance", "compartment layout"],
         ["day hikers", "multi day trips", "kayakers"]),
        ("light", ["headlamp", "lantern", "camp light string"],
         ["brightness", "battery life", "water resistance"],
         ["night hikers", "camping", "power cuts"]),
        ("seating", ["camping chair", "hammock", "folding stool"],
         ["weight capacity", "packed size", "comfort"],
         ["car campers", "festivals", "backpackers"]),
        ("navigation", ["gps watch", "compass", "trail map case"],
         ["battery life", "accuracy", "readability"],
         ["backpackers", "trail runners", "remote hikes"]),
        ("clothing", ["rain jacket", "insulated vest", "hiking socks"],
         ["waterproofing", "breathability", "packability"],
         ["hikers", "wet climates", "layering"]),
    ],
    "home office": [
        ("desk", ["standing desk", "l shaped desk", "lap desk"],
         ["height range", "stability", "cable management"],
         ["small rooms", "dual monitor setups", "back pain sufferers"]),
        ("chair", ["ergonomic office chair", "mesh office chair", "kneeling chair"],
         ["lumbar support", "adjustability", "breathability"],
         ["long workdays", "back pain sufferers", "shared desks"]),
        ("lighting", ["desk lamp", "monitor light bar", "ring light"],
         ["colour temperature range", "glare control", "flicker free dimming"],
         ["night workers", "video calls", "shared bedrooms"]),
        ("audio", ["noise cancelling headphones", "usb microphone", "desk speakers"],
         ["isolation", "voice clarity", "latency"],
         ["open plan offices", "meeting heavy weeks", "shared spaces"]),
        ("organisation", ["desk organiser", "monitor stand", "cable management kit"],
         ["compartment layout", "build rigidity", "footprint"],
         ["small desks", "cluttered setups", "hot desking"]),
        ("air", ["desk fan", "small humidifier", "air purifier"],
         ["noise level", "coverage area", "filter life"],
         ["stuffy rooms", "dry climates", "allergy sufferers"]),
        ("breaks", ["footrest", "wrist rest", "posture corrector"],
         ["adjustability", "material comfort", "size range"],
         ["desk workers", "short users", "wrist pain"]),
    ],
    "garden": [
        ("watering", ["garden hose", "hose reel", "watering can"],
         ["kink resistance", "connector fit", "capacity"],
         ["small gardens", "raised beds", "balconies"]),
        ("tools", ["garden trowel", "pruning shears", "garden fork"],
         ["blade quality", "handle comfort", "rust resistance"],
         ["container gardening", "beginner gardeners", "raised beds"]),
        ("growing", ["grow light", "seed starting tray", "plant pots"],
         ["spectrum", "drainage", "depth"],
         ["indoor plants", "low light rooms", "seed starting"]),
        ("soil", ["compost bin", "soil tester", "mulch"],
         ["capacity", "accuracy", "coverage area"],
         ["kitchen gardeners", "small gardens", "poor soil"]),
        ("protection", ["garden fence", "plant cages", "bird netting"],
         ["durability", "assembly", "coverage area"],
         ["vegetable patches", "deer areas", "raised beds"]),
        ("furniture", ["patio umbrella", "garden bench", "outdoor cushions"],
         ["weather resistance", "uv stability", "foldability"],
         ["small patios", "sunny gardens", "renters"]),
        ("lawn", ["lawn mower", "grass trimmer", "lawn aerator"],
         ["cut width", "battery runtime", "noise level"],
         ["small lawns", "steep slopes", "quiet neighbourhoods"]),
    ],
    "baby & kids": [
        ("feeding", ["baby bottle", "bottle steriliser", "baby food maker"],
         ["anti colic venting", "cleaning ease", "capacity"],
         ["new parents", "twins", "combo feeding"]),
        ("sleep", ["baby monitor", "sleep sack", "white noise machine"],
         ["range", "night vision", "battery life"],
         ["new parents", "shared rooms", "light sleepers"]),
        ("travel", ["baby carrier", "stroller", "car seat"],
         ["weight range", "foldability", "harness fit"],
         ["city walks", "frequent travellers", "apartment living"]),
        ("safety", ["baby gate", "outlet covers", "cabinet locks"],
         ["mounting type", "width range", "one hand operation"],
         ["renters", "stairs", "new crawlers"]),
        ("play", ["play mat", "building blocks", "stacking toys"],
         ["material safety", "washability", "age range"],
         ["small spaces", "tummy time", "toddlers"]),
        ("bath", ["baby bath tub", "bath thermometer", "hooded towel"],
         ["non slip surface", "drain ease", "absorbency"],
         ["new parents", "small bathrooms", "sensitive skin"]),
        ("kids daily", ["kids water bottle", "kids backpack", "kids utensil set"],
         ["leak proofing", "strap comfort", "material safety"],
         ["school age", "daycare", "picky eaters"]),
    ],
    "pets": [
        ("bed", ["dog bed", "orthopedic dog bed", "cat bed"],
         ["washability", "support", "chew resistance"],
         ["senior dogs", "small apartments", "shedders"]),
        ("feeding", ["slow feeder bowl", "pet water fountain", "automatic feeder"],
         ["flow rate", "portion accuracy", "cleaning ease"],
         ["fast eaters", "cats", "long workdays"]),
        ("walk", ["dog leash", "dog harness", "poop bag dispenser"],
         ["pull rating", "fit adjustability", "reflective stitching"],
         ["pullers", "night walks", "puppies"]),
        ("litter", ["cat litter box", "litter mat", "litter scoop"],
         ["odour sealing", "entry height", "sifting ease"],
         ["small apartments", "multi cat homes", "senior cats"]),
        ("play", ["cat tree", "cat scratcher", "interactive dog toy"],
         ["stability", "material safety", "refill availability"],
         ["indoor cats", "chewers", "bored pets"]),
        ("grooming", ["pet grooming brush", "nail clipper", "pet shampoo"],
         ["coat type fit", "guard safety", "rinse ease"],
         ["double coated breeds", "nervous pets", "shedding season"]),
        ("travel", ["pet carrier", "dog crate", "car seat cover"],
         ["airline size limits", "ventilation", "foldability"],
         ["vet trips", "frequent travellers", "car rides"]),
    ],
    "home cleaning": [
        ("vacuum", ["robot vacuum", "stick vacuum", "handheld vacuum"],
         ["suction power", "battery runtime", "bin capacity"],
         ["pet owners", "hardwood floors", "small apartments"]),
        ("mop", ["steam mop", "spray mop", "microfiber mop"],
         ["pad washability", "tank capacity", "floor safety"],
         ["tile floors", "hardwood floors", "quick cleans"]),
        ("deep clean", ["carpet cleaner", "upholstery cleaner", "grout brush"],
         ["stain lift", "drying time", "brush reach"],
         ["pet households", "renters", "allergy sufferers"]),
        ("dusting", ["duster with extension", "microfiber cloth set", "electrostatic duster"],
         ["reach", "washability", "static hold"],
         ["high ceilings", "ceiling fans", "quick cleans"]),
        ("windows", ["window vacuum", "squeegee", "glass cleaner concentrate"],
         ["streak free finish", "edge reach", "tank capacity"],
         ["large windows", "shower screens", "tall rooms"]),
        ("laundry room", ["lint remover", "drying rack", "stain pen"],
         ["fabric safety", "foldability", "stain types covered"],
         ["small homes", "wool wearers", "families"]),
        ("odour", ["air purifier", "odour absorber", "dehumidifier"],
         ["coverage area", "filter life", "noise level"],
         ["damp rooms", "pet households", "allergy sufferers"]),
    ],
    "car": [
        ("emergency", ["jump starter", "tire inflator", "emergency kit"],
         ["peak amps", "pressure accuracy", "case organisation"],
         ["cold climates", "long commutes", "new drivers"]),
        ("interior", ["car vacuum", "seat gap filler", "trunk organiser"],
         ["suction power", "fit", "compartment layout"],
         ["families", "pet owners", "commuters"]),
        ("mounts", ["car phone mount", "dashboard mat", "cup holder expander"],
         ["grip strength", "one hand operation", "vent fit"],
         ["rideshare drivers", "long commutes", "navigation users"]),
        ("recording", ["dash cam", "rear view camera", "obd2 scanner"],
         ["video quality", "night performance", "app quality"],
         ["new drivers", "fleet use", "diy diagnostics"]),
        ("care", ["car wax", "microfiber towel set", "car wash mitt"],
         ["beading", "scratch risk", "absorbency"],
         ["dark paint", "weekend washers", "hot climates"]),
        ("comfort", ["car sun shade", "seat cushion", "steering wheel cover"],
         ["heat blocking", "fit", "breathability"],
         ["hot climates", "long drives", "back pain sufferers"]),
    ],
    "coffee & espresso": [
        ("grinding", ["burr coffee grinder", "blade grinder", "hand grinder"],
         ["consistent grind", "noise level", "static control"],
         ["espresso drinkers", "pour over fans", "quiet kitchens"]),
        ("brewing", ["espresso machine", "french press", "moka pot"],
         ["temperature stability", "pressure range", "cleaning ease"],
         ["daily drinkers", "small kitchens", "beginners"]),
        ("milk", ["milk frother", "steam wand machine", "milk pitcher"],
         ["foam texture", "cleaning ease", "capacity"],
         ["latte drinkers", "dairy free households", "busy mornings"]),
        ("cold", ["cold brew maker", "iced coffee maker"],
         ["steep time", "filter quality", "fridge fit"],
         ["hot climates", "batch brewers", "small fridges"]),
        ("accessories", ["coffee scale", "tamper", "reusable coffee filter"],
         ["accuracy", "fit", "cleaning ease"],
         ["precise brewers", "espresso drinkers", "waste reducers"]),
        ("storage", ["coffee canister", "bean storage container"],
         ["airtight seal", "co2 valve", "capacity"],
         ["bulk buyers", "daily drinkers", "humid climates"]),
    ],
    "budget tech": [
        ("audio budget", ["budget wireless earbuds", "budget bluetooth speaker"],
         ["battery life", "water resistance", "pairing stability"],
         ["students", "commuters", "first time buyers"]),
        ("wearables", ["budget fitness tracker", "budget smartwatch"],
         ["battery life", "sensor accuracy", "app quality"],
         ["beginners", "students", "gift buyers"]),
        ("computing", ["budget tablet", "refurbished laptop"],
         ["screen quality", "battery life", "update support"],
         ["students", "media watchers", "second device"]),
        ("home", ["budget smart plug", "budget security camera"],
         ["app quality", "connectivity", "local storage"],
         ["renters", "first smart homes", "tight budgets"]),
        ("accessories", ["budget power bank", "budget usb hub", "budget webcam"],
         ["charging speed", "port selection", "video quality"],
         ["students", "desk setups", "video calls"]),
    ],
    "tools": [
        ("drilling", ["cordless drill", "impact driver", "hammer drill"],
         ["torque", "battery platform", "chuck quality"],
         ["diy beginners", "weekend projects", "one battery system"]),
        ("fastening", ["socket set", "screwdriver set", "torque wrench"],
         ["steel grade", "ratchet teeth", "case organisation"],
         ["car work", "home repairs", "beginners"]),
        ("measuring", ["laser level", "stud finder", "tape measure", "multimeter"],
         ["accuracy", "readability", "range"],
         ["hanging shelves", "wiring work", "diy beginners"]),
        ("lighting", ["work light", "head torch", "inspection lamp"],
         ["brightness", "battery runtime", "impact resistance"],
         ["garages", "evening projects", "tight spaces"]),
        ("cutting", ["circular saw", "jigsaw", "utility knife"],
         ["cut depth", "blade change ease", "dust extraction"],
         ["sheet goods", "curved cuts", "quick jobs"]),
        ("storage", ["tool box", "tool bag", "pegboard"],
         ["compartment layout", "carry comfort", "wall footprint"],
         ["small garages", "mobile trades", "shared sheds"]),
        ("finishing", ["heat gun", "sandpaper set", "paint sprayer"],
         ["temperature range", "grit spread", "overspray control"],
         ["furniture restores", "detail work", "large surfaces"]),
    ],
    "travel": [
        ("luggage", ["carry on luggage", "checked suitcase", "duffel bag"],
         ["airline size limits", "wheel quality", "weight"],
         ["carry on only", "frequent flyers", "family trips"]),
        ("packing", ["packing cubes", "compression bags", "toiletry bag"],
         ["size range", "zipper durability", "compressibility"],
         ["light packers", "business trips", "long trips"]),
        ("comfort", ["travel pillow", "eye mask", "compression socks"],
         ["neck support", "packed size", "breathability"],
         ["long haul flights", "light sleepers", "long drives"]),
        ("power", ["travel adapter", "portable charger", "cable organiser"],
         ["plug coverage", "charging speed", "pocket size"],
         ["multi country trips", "digital nomads", "carry on only"]),
        ("security", ["luggage lock", "money belt", "rfid blocking wallet"],
         ["tsa acceptance", "wearing comfort", "card capacity"],
         ["hostel stays", "crowded cities", "business trips"]),
        ("gear", ["garment bag", "luggage scale", "foldable daypack"],
         ["crease control", "accuracy", "packed size"],
         ["wedding trips", "weight limit airlines", "day trips"]),
    ],
    "sleep": [
        ("mattress", ["mattress topper", "mattress protector", "air mattress"],
         ["firmness range", "heat retention", "washability"],
         ["side sleepers", "back sleepers", "hot sleepers"]),
        ("pillow", ["memory foam pillow", "cooling pillow", "body pillow"],
         ["loft", "support", "breathability"],
         ["side sleepers", "neck pain", "hot sleepers"]),
        ("bedding", ["weighted blanket", "cotton sheets", "duvet"],
         ["weight range", "thread count", "warmth rating"],
         ["anxious sleepers", "hot sleepers", "cold climates"]),
        ("darkness", ["blackout curtains", "sleep mask", "window film"],
         ["light blocking", "mounting type", "breathability"],
         ["shift workers", "street lit rooms", "light sleepers"]),
        ("sound", ["white noise machine", "bed fan", "ear plugs"],
         ["sound range", "timer options", "fit comfort"],
         ["noisy neighbours", "light sleepers", "shared rooms"]),
        ("tracking", ["sleep tracker", "bedside lamp"],
         ["accuracy", "app quality", "dimming range"],
         ["data driven sleepers", "readers", "night feeds"]),
    ],
    "personal care": [
        ("oral", ["electric toothbrush", "water flosser", "tongue scraper"],
         ["brush head availability", "battery life", "pressure sensor"],
         ["braces wearers", "sensitive gums", "travellers"]),
        ("hair", ["hair dryer", "straightener", "curling wand"],
         ["heat control", "ionic output", "weight"],
         ["thick hair", "fine hair", "quick mornings"]),
        ("shaving", ["beard trimmer", "electric shaver", "nose hair trimmer"],
         ["blade sharpness", "wet and dry use", "battery life"],
         ["sensitive skin", "daily shavers", "travellers"]),
        ("skin", ["facial cleansing brush", "led face mask", "derma roller"],
         ["intensity range", "cleaning ease", "material safety"],
         ["sensitive skin", "acne prone skin", "mature skin"]),
        ("bath", ["shower filter", "bath mat", "loofah set"],
         ["filter life", "non slip surface", "drying speed"],
         ["hard water areas", "small bathrooms", "sensitive skin"]),
        ("grooming", ["manicure kit", "tweezers", "makeup mirror"],
         ["steel grade", "grip precision", "lighting quality"],
         ["home grooming", "travellers", "detail work"]),
    ],
    "smart home": [
        ("plugs", ["smart plug", "smart power strip", "smart bulb"],
         ["app quality", "energy monitoring", "connectivity"],
         ["alexa users", "renters", "first smart homes"]),
        ("security", ["video doorbell", "smart lock", "security camera"],
         ["night performance", "battery life", "local storage"],
         ["renters", "families", "package heavy households"]),
        ("climate", ["smart thermostat", "smart fan", "air quality monitor"],
         ["scheduling", "sensor accuracy", "hvac compatibility"],
         ["older houses", "energy savers", "allergy sufferers"]),
        ("lighting", ["led strip lights", "smart lamp", "motion sensor light"],
         ["colour range", "adhesive quality", "detection range"],
         ["renters", "hallways", "media rooms"]),
        ("hubs", ["smart speaker", "smart display", "hub bridge"],
         ["protocol support", "voice accuracy", "privacy controls"],
         ["mixed ecosystems", "kitchens", "multi room audio"]),
        ("garden", ["smart irrigation controller", "smart plant sensor"],
         ["zone count", "weather awareness", "battery life"],
         ["travellers", "raised beds", "forgetful waterers"]),
    ],
    "audio": [
        ("headphones", ["noise cancelling headphones", "open back headphones", "earbuds"],
         ["isolation", "soundstage", "battery life"],
         ["commuters", "open plan offices", "gym goers"]),
        ("speakers", ["bookshelf speakers", "bluetooth speaker", "soundbar"],
         ["bass response", "connectivity", "room coverage"],
         ["small rooms", "tv setups", "vinyl collectors"]),
        ("recording", ["usb microphone", "audio interface", "studio headphones"],
         ["self noise", "latency", "flat response"],
         ["podcasters", "streamers", "home studios"]),
        ("turntable", ["turntable", "phono preamp", "record cleaning kit"],
         ["wow and flutter", "cartridge quality", "brush softness"],
         ["vinyl collectors", "beginners", "archive digitising"]),
        ("portable", ["portable speaker", "bone conduction headphones", "mp3 player"],
         ["water resistance", "battery life", "fit stability"],
         ["runners", "beach trips", "swimmers"]),
    ],
    # ---- expansion niches -------------------------------------------------
    "health & wellness": [
        ("monitoring", ["blood pressure monitor", "pulse oximeter", "digital body scale"],
         ["accuracy", "cuff size range", "app quality"],
         ["seniors", "home monitoring", "data trackers"]),
        ("pain relief", ["heating pad", "massage gun", "posture corrector"],
         ["temperature range", "auto shut off", "fit adjustability"],
         ["back pain sufferers", "desk workers", "period pain"]),
        ("mobility", ["knee brace", "resistance bands", "walking cane"],
         ["compression level", "grip comfort", "weight capacity"],
         ["runners", "rehab users", "seniors"]),
        ("air quality", ["humidifier", "air purifier", "nebuliser"],
         ["coverage area", "noise level", "filter life"],
         ["dry climates", "allergy sufferers", "nurseries"]),
        ("light therapy", ["light therapy lamp", "red light panel", "sunrise alarm clock"],
         ["lux output", "timer options", "flicker free dimming"],
         ["winter blues", "shift workers", "seasonal low mood"]),
        ("supplements", ["pill organiser", "protein shaker", "vitamin dispenser"],
         ["compartment count", "leak proofing", "portion accuracy"],
         ["travellers", "gym goers", "seniors"]),
    ],
    "office & school supplies": [
        ("paper", ["dotted notebook", "spiral notebook", "legal pad"],
         ["paper weight", "lay flat binding", "bleed through"],
         ["fountain pen users", "students", "bullet journalers"]),
        ("writing", ["gel pen set", "mechanical pencil", "highlighter set"],
         ["ink flow", "grip comfort", "smear resistance"],
         ["left handed writers", "note takers", "exam season"]),
        ("desk", ["desk planner", "file organiser", "monitor riser"],
         ["compartment layout", "build rigidity", "footprint"],
         ["small desks", "students", "home offices"]),
        ("machines", ["label maker", "paper shredder", "stapler"],
         ["sheet capacity", "noise level", "jam resistance"],
         ["home offices", "families", "small businesses"]),
        ("board", ["whiteboard", "cork board", "planner wall calendar"],
         ["ghosting resistance", "mounting type", "surface size"],
         ["shared offices", "students", "kitchens"]),
        ("carry", ["binder", "pencil case", "folder set"],
         ["ring quality", "zipper durability", "tab count"],
         ["students", "commuters", "project sorting"]),
    ],
    "fashion & accessories": [
        ("wallet", ["leather wallet", "rfid blocking wallet", "card holder"],
         ["leather grade", "card capacity", "stitching quality"],
         ["minimalists", "frequent travellers", "gift buyers"]),
        ("bags", ["crossbody bag", "tote bag", "anti theft backpack"],
         ["strap adjustability", "lining quality", "zipper durability"],
         ["commuters", "parents", "city walkers"]),
        ("eyewear", ["polarised sunglasses", "blue light glasses", "reading glasses"],
         ["lens quality", "frame fit", "uv protection"],
         ["drivers", "screen heavy jobs", "light sensitive eyes"]),
        ("watch", ["minimalist watch", "field watch", "smart watch"],
         ["movement type", "water resistance", "strap comfort"],
         ["office wear", "outdoor use", "first watch buyers"]),
        ("belts", ["leather belt", "ratchet belt", "canvas belt"],
         ["buckle quality", "sizing accuracy", "leather grade"],
         ["daily wear", "weight fluctuation", "office wear"]),
        ("cold weather", ["cashmere scarf", "touchscreen gloves", "wool beanie"],
         ["warmth to weight", "softness", "fit"],
         ["commuters", "cold climates", "gift buyers"]),
        ("jewellery care", ["jewellery organiser", "ultrasonic cleaner", "travel jewellery case"],
         ["compartment layout", "tarnish protection", "packed size"],
         ["small spaces", "frequent travellers", "gift buyers"]),
    ],
    "photography & video": [
        ("support", ["phone tripod", "camera tripod", "smartphone gimbal"],
         ["stability", "load capacity", "packed size"],
         ["vloggers", "product shots", "travellers"]),
        ("lighting", ["ring light", "softbox kit", "led video light"],
         ["colour accuracy", "dimming smoothness", "power options"],
         ["video calls", "product photography", "home studios"]),
        ("storage", ["sd card", "external ssd", "camera backpack"],
         ["write speed", "shock resistance", "compartment layout"],
         ["4k video", "wedding shooters", "hikers"]),
        ("audio", ["lavalier microphone", "shotgun microphone", "portable recorder"],
         ["self noise", "wind protection", "battery life"],
         ["interviews", "run and gun shoots", "podcasters"]),
        ("backdrop", ["photo backdrop", "backdrop stand", "reflectors"],
         ["crease resistance", "setup time", "surface finish"],
         ["home studios", "product shots", "small rooms"]),
        ("lens care", ["lens filter", "sensor cleaning kit", "lens pen"],
         ["coating quality", "thread fit", "lint free wipes"],
         ["landscape shooters", "dusty locations", "daily carry"]),
    ],
    "gaming": [
        ("seating", ["gaming chair", "racing seat", "floor gaming chair"],
         ["lumbar support", "recline range", "build rigidity"],
         ["long sessions", "back pain sufferers", "small rooms"]),
        ("input", ["mechanical keyboard", "gaming mouse", "wireless controller"],
         ["switch feel", "sensor accuracy", "latency"],
         ["fps players", "console players", "quiet households"]),
        ("audio", ["gaming headset", "desktop speakers", "microphone"],
         ["positional accuracy", "mic clarity", "comfort clamp"],
         ["team chat", "streamers", "long sessions"]),
        ("display", ["gaming monitor", "portable monitor", "tv for gaming"],
         ["refresh rate", "input lag", "panel quality"],
         ["fps players", "console players", "small desks"]),
        ("desk", ["gaming desk", "mouse pad xl", "monitor arm"],
         ["surface size", "cable management", "load capacity"],
         ["multi monitor setups", "small rooms", "streaming desks"]),
        ("streaming", ["capture card", "stream deck", "webcam"],
         ["passthrough quality", "button count", "low light performance"],
         ["console streamers", "beginners", "multi scene setups"]),
        ("cooling", ["laptop cooling pad", "controller charging dock"],
         ["airflow", "noise level", "device fit"],
         ["hot rooms", "long sessions", "cluttered desks"]),
    ],
    "food & pantry": [
        ("storage", ["food storage containers", "spice rack", "pantry organiser"],
         ["airtight seal", "stackability", "labelling"],
         ["bulk shoppers", "small kitchens", "meal preppers"]),
        ("preservation", ["vacuum sealer", "dehydrator", "freezer bags"],
         ["seal strength", "tray area", "puncture resistance"],
         ["batch cooking", "gardeners", "hunters"]),
        ("staples", ["olive oil", "raw honey", "protein powder"],
         ["sourcing transparency", "container quality", "mixability"],
         ["daily cooking", "health focused buyers", "gym goers"]),
        ("drinks", ["water filter pitcher", "sparkling water maker", "tea infuser"],
         ["filter life", "carbonation control", "mesh fineness"],
         ["hard water areas", "soda drinkers", "loose leaf tea"]),
        ("snacks", ["bento box", "snack containers", "insulated lunch bag"],
         ["leak proofing", "compartment layout", "cold retention"],
         ["school lunches", "office workers", "picnics"]),
        ("cooking staples", ["cast iron skillet", "dutch oven", "mortar and pestle"],
         ["seasoning quality", "heat retention", "grinding surface"],
         ["slow cooking", "bread baking", "spice grinding"]),
    ],
    "home decor": [
        ("lighting", ["floor lamp", "table lamp", "led strip lights"],
         ["dimming range", "shade quality", "footprint"],
         ["renters", "reading corners", "dark hallways"]),
        ("rugs", ["area rug", "runner rug", "outdoor rug"],
         ["pile height", "non slip backing", "cleaning ease"],
         ["hardwood floors", "high traffic rooms", "pet households"]),
        ("walls", ["wall mirror", "picture frames", "wall shelf"],
         ["mounting hardware", "glass quality", "load capacity"],
         ["renters", "small rooms", "gallery walls"]),
        ("textiles", ["throw blanket", "cushion covers", "curtain rods"],
         ["fabric weight", "washability", "rod diameter"],
         ["cold rooms", "seasonal refreshes", "bay windows"]),
        ("vessels", ["ceramic vase", "planter pot", "decorative bowl"],
         ["glaze quality", "drainage", "size range"],
         ["dried flowers", "indoor plants", "shelf styling"]),
        ("organisation decor", ["storage baskets", "entryway organiser", "key holder"],
         ["material durability", "capacity", "wall footprint"],
         ["small entryways", "family homes", "renters"]),
    ],
    "security & safety": [
        ("cameras", ["outdoor security camera", "indoor camera", "peephole camera"],
         ["night performance", "local storage", "weatherproofing"],
         ["renters", "package heavy households", "driveways"]),
        ("detection", ["smoke detector", "carbon monoxide detector", "water leak sensor"],
         ["alarm loudness", "battery life", "false alarm rate"],
         ["older houses", "renters", "basements"]),
        ("locks", ["keypad door lock", "smart lock", "deadbolt"],
         ["code capacity", "battery life", "door thickness fit"],
         ["renters", "key losing households", "side doors"]),
        ("safe", ["fireproof safe", "document safe", "key safe"],
         ["fire rating", "bolt count", "wall mounting"],
         ["documents", "jewellery", "holiday keys"]),
        ("first aid", ["first aid kit", "emergency blanket", "burn gel"],
         ["item coverage", "case organisation", "expiry dates"],
         ["families", "hikers", "workplaces"]),
        ("fire", ["fire extinguisher", "fire blanket", "escape ladder"],
         ["rating class", "reach", "deployment speed"],
         ["kitchens", "upper floors", "garages"]),
        ("lighting", ["motion sensor light", "solar path light", "gate light"],
         ["detection range", "brightness", "weatherproofing"],
         ["dark driveways", "renters", "late arrivals"]),
    ],
    "crafts & hobbies": [
        ("sewing", ["sewing machine", "fabric scissors", "sewing machine needle set"],
         ["stitch range", "blade sharpness", "needle variety"],
         ["beginners", "garment makers", "quilting"]),
        ("cutting", ["cutting mat", "rotary cutter", "craft knife"],
         ["self healing surface", "blade change ease", "grid accuracy"],
         ["quilting", "paper crafts", "left handed cutters"]),
        ("adhesive", ["glue gun", "fabric glue", "double sided tape"],
         ["heat up time", "bond strength", "residue"],
         ["quick repairs", "fabric work", "scrapbooking"]),
        ("yarn crafts", ["yarn", "crochet hooks", "knitting needles"],
         ["fibre content", "hook size range", "grip comfort"],
         ["beginners", "blanket makers", "arthritis hands"]),
        ("painting", ["acrylic paint set", "paint brush set", "canvas pack"],
         ["pigment load", "bristle quality", "surface priming"],
         ["beginners", "detail work", "large canvases"]),
        ("paper crafts", ["sketchbook", "paper trimmer", "stamping kit"],
         ["paper weight", "cut accuracy", "ink drying time"],
         ["journaling", "card making", "travel sketching"]),
        ("maker", ["heat press", "3d printer pen", "soldering iron"],
         ["temperature control", "plate size", "tip range"],
         ["t shirt makers", "small repairs", "electronics hobbies"]),
    ],
    "laundry & storage": [
        ("washing", ["portable washing machine", "laundry detergent sheets", "stain remover"],
         ["load capacity", "water use", "fabric safety"],
         ["renters", "campervans", "small homes"]),
        ("drying", ["drying rack", "clothes line", "garment steamer"],
         ["rack area", "foldability", "steam output"],
         ["apartments", "delicates", "quick turnarounds"]),
        ("ironing", ["steam iron", "ironing board", "ironing mat"],
         ["steam output", "board stability", "heat resistance"],
         ["shirt wearers", "small homes", "travellers"]),
        ("baskets", ["laundry hamper", "sorting basket", "mesh wash bag"],
         ["capacity", "handle strength", "fabric protection"],
         ["families", "small bathrooms", "delicates"]),
        ("storage bins", ["storage bins", "under bed storage", "vacuum storage bags"],
         ["stackability", "lid seal", "compression ratio"],
         ["seasonal clothes", "small homes", "moving house"]),
        ("closet", ["closet organiser", "shoe rack", "hanger set"],
         ["shelf adjustability", "tier count", "hanger grip"],
         ["renters", "shared wardrobes", "small closets"]),
        ("garment care", ["lint remover", "fabric shaver", "clothes brush"],
         ["blade safety", "battery life", "fabric types"],
         ["wool wearers", "pilling prone fabrics", "workwear"]),
    ],
}

# ---------------------------------------------------------------------------
# Sentence shapes. Each returns one keyword string from a product + extras.
# They mirror the shapes already present in keywords.csv so keywords.py
# extracts the same `base`, `budget`, `audience` and `intent` fields.
# ---------------------------------------------------------------------------
PATTERNS: list[tuple[str, str]] = [
    # ---- transactional (high intent - these are the money keywords) ----
    ("best {p} for {aud}", "high"),
    ("best {p} under {price} dollars", "high"),
    ("{p} for {aud}", "high"),
    ("{p} for beginners", "high"),
    ("affordable {p} that does not feel cheap", "high"),
    ("{p} for small spaces", "medium"),
    ("{p} for {aud} on a budget", "high"),
    ("which {p} should you buy", "high"),
    ("{p}: which one is right for {aud}", "high"),
    # ---- single-product review angles (the shape the writer actually has) ----
    ("{p} review: {feat} in real use", "high"),
    ("{p}: {feat} rated against real use", "medium"),
    ("{p} review for {aud}", "high"),
    ("{p} under {price} dollars: honest verdict", "high"),
    ("is {art} {p} still worth buying in {year}", "high"),
    ("{p} for {aud}: {feat} explained", "medium"),
    ("what {feat} really means in {art} {p}", "medium"),
    ("{p} pros and cons for {aud}", "high"),
    ("{p}: the honest verdict for {aud}", "high"),
    ("{p} for {problem}", "medium"),
    ("{p} that actually solve {problem}", "medium"),
    # ---- commercial investigation / guide ----
    ("is {art} {p} worth it for {aud}", "medium"),
    ("{p} buying guide {year}", "medium"),
    ("common {p} mistakes and how to avoid them", "medium"),
    ("{p} alternatives that cost less", "medium"),
    ("how to choose {art} {p} in 5 minutes", "medium"),
    ("what to check before buying {art} {p}", "medium"),
    ("{p}: what to check before buying", "medium"),
    # ---- informational (low intent, builds topical authority) ----
    ("cheap {p} that actually lasts", "low"),
    ("how long does {art} {p} last", "low"),
    ("do you really need {art} {p}", "low"),
    ("{p} under {price} dollars worth checking", "low"),
    ("most overrated {p} features", "low"),
    ("{p} vs traditional alternatives", "low"),
    ("how to clean {art} {p} properly", "low"),
    ("signs it is time to replace your {p}", "low"),
    ("how often should you replace {art} {p}", "low"),
]

# shapes where the product noun must stay grammatical after an article
COMPARE_SHAPES = ["{a} or {b}: which should you buy", "{a} vs {b} for {aud}"]


# Products that end in "s" but are singular nouns - the article rules differ.
SINGULAR_S = {
    "air mattress", "compass", "dog harness", "french press", "heat press",
}


def is_plural(product: str) -> bool:
    """True for nouns that take no article ("adjustable dumbbells", "tongs").

    Getting this wrong is what produces "do you really need an adjustable
    dumbbells" - a keyword no human ever types and a title the article engine
    then has to fight with.
    """
    p = product.strip().lower()
    return p.endswith("s") and p not in SINGULAR_S


def article(word: str) -> str:
    """a / an - decided by sound, not by the first letter."""
    w = (word or "").strip().lower()
    if not w:
        return "a"
    if w.startswith(("hour", "honest", "honor", "honour", "heir")):
        return "an"
    if w.startswith(("uni", "use", "user", "useful", "usual", "eu", "one ", "once")):
        return "a"
    return "an" if w[0] in "aeiou" else "a"


PRICES = [20, 25, 30, 40, 50, 60, 75, 100, 150, 200, 250, 300]
YEARS = [2026, 2027]
PROBLEMS = [
    "a tiny kitchen", "a noisy household", "dusty rooms", "limited storage",
    "long workdays", "fussy eaters", "wet climates", "hard floors",
    "shared spaces", "early mornings", "late shifts", "small budgets",
]


def build_rows(seed: int = SEED) -> list[dict]:
    """Generate candidate rows (not yet de-duplicated)."""
    rng = random.Random(seed)
    rows: list[dict] = []
    seen_local: set[str] = set()

    for niche, families in CATALOG.items():
        for _family_label, products, features, audiences in families:
            for product in products:
                # every product gets a solid spread of shapes, deterministic order
                rng.shuffle(PROBLEMS)
                picks = list(range(len(PATTERNS)))
                rng.shuffle(picks)
                want = min(len(PATTERNS), 5 + rng.randint(0, 3))
                plural = is_plural(product)
                for shape_idx in picks[:want]:
                    shape, priority = PATTERNS[shape_idx]
                    # "a/an <noun>" only reads correctly for singular products
                    feature = features[rng.randrange(len(features))] if features else ""
                    audience = audiences[rng.randrange(len(audiences))] if audiences else ""
                    if plural and "{art}" in shape:
                        continue
                    kw = shape.format(
                        p=product,
                        art=article(product),
                        aud=audience or "daily use",
                        feat=feature or "build quality",
                        price=PRICES[rng.randrange(len(PRICES))],
                        year=YEARS[rng.randrange(len(YEARS))],
                        problem=PROBLEMS[rng.randrange(len(PROBLEMS))],
                    )
                    key = kw.lower().strip()
                    if key in seen_local or len(product.split()) > 4:
                        continue
                    seen_local.add(key)
                    rows.append({
                        "keyword": kw,
                        "niche": niche,
                        "feature": feature,
                        "audience": audience,
                        "asin": "",
                        "priority": priority,
                    })

            # sibling comparisons inside the family - the highest-value shape,
            # and the only one that unlocks topic_planner's comparison template
            for i, a in enumerate(products):
                for b in products[i + 1:]:
                    pair_aud = audiences[i % len(audiences)] if audiences else "daily use"
                    # two comparison shapes per pair is plenty: the planner only
                    # writes the comparison template ~24% of the time, so a pool
                    # that is half comparisons just starves the other shapes
                    for shape, priority in ((f"{a} vs {b}", "high"),
                                            (COMPARE_SHAPES[0].format(a=a, b=b), "high")):
                        key = shape.lower().strip()
                        if key in seen_local:
                            continue
                        seen_local.add(key)
                        rows.append({
                            "keyword": shape,
                            "niche": niche,
                            "feature": features[0] if features else "",
                            "audience": pair_aud,
                            "asin": "",
                            "priority": priority,
                        })
    return rows


def read_existing() -> tuple[list[str], set[str]]:
    """(raw file lines, normalised keyword set)."""
    with KEYWORDS_CSV.open("r", encoding="utf-8-sig", newline="") as fh:
        raw = fh.read()
    lines = raw.split("\r\n") if "\r\n" in raw else raw.split("\n")
    existing = set()
    for line in lines[1:]:
        if not line.strip():
            continue
        cells = next(csv.reader([line]))
        if cells and cells[0].strip():
            existing.add(cells[0].strip().lower())
    return lines, existing


def report() -> dict:
    """Snapshot of the current pool: size, spread, and how long it lasts."""
    rows = list(csv.DictReader(KEYWORDS_CSV.read_text(encoding="utf-8-sig").splitlines()))
    per_niche = Counter(r["niche"] for r in rows)
    per_priority = Counter(r["priority"] for r in rows)
    # families with 2+ distinct bases are the ones that can produce comparisons
    fams: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        base = r["keyword"].lower()
        for pre in ("best ", "cheap ", "is a ", "do you really need a ", "how to choose a ",
                    "what to check before buying a ", "most overrated ", "how long does a "):
            if base.startswith(pre):
                base = base[len(pre):]
                break
        for suf in (" buying guide 2026", " buying guide 2027", " for small spaces",
                    " for beginners", " alternatives that cost less",
                    " mistakes and how to avoid them", ": what to check before buying",
                    " vs traditional alternatives", " that actually last"):
            if base.endswith(suf):
                base = base[: -len(suf)]
                break
        fams[r["niche"] + "|" + base.split()[0] if base.split() else r["niche"]].add(base)
    comparable = sum(1 for v in fams.values() if len(v) >= 2)
    return {
        "total_rows": len(rows),
        "niches": len(per_niche),
        "comparable_families": comparable,
        "priority_mix": dict(per_priority),
        "years_at_3_per_day": round(len(rows) / (3 * 365), 1),
        "years_at_6_per_day": round(len(rows) / (6 * 365), 1),
        "per_niche": dict(per_niche.most_common()),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Grow data/keywords.csv deterministically")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--stats", action="store_true", help="print the current pool report")
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args(argv)

    if args.stats:
        print(report())
        return 0

    lines, existing = read_existing()
    before = len(existing)

    candidates = build_rows(args.seed)
    fresh, dropped = [], 0
    local = set()
    for row in candidates:
        key = row["keyword"].strip().lower()
        if key in existing or key in local:
            dropped += 1
            continue
        local.add(key)
        fresh.append(row)

    if args.dry_run:
        per_niche = Counter(r["niche"] for r in fresh)
        print(f"existing rows      : {before}")
        print(f"generated          : {len(candidates)}")
        print(f"already present    : {dropped}")
        print(f"would be added     : {len(fresh)}")
        print(f"new total          : {before + len(fresh)}")
        have = Counter()
        for line in lines[1:]:
            if line.strip():
                cells = next(csv.reader([line]))
                if len(cells) > 1:
                    have[cells[1]] += 1
        print("\nper niche (new / total):")
        for niche, n in per_niche.most_common():
            print(f"  {niche:<26} +{n:>5}   -> {have.get(niche, 0) + n:>5}")
        print("\nsamples:")
        for row in fresh[:15]:
            print(f"  [{row['priority']:<6}] {row['keyword']}")
        return 0

    # append with the same CRLF endings and 6-column width
    out_lines = [ln for ln in lines if ln.strip() != ""]
    out_lines.extend(_csv_line([row[c] for c in HEADER]) for row in fresh)
    KEYWORDS_CSV.write_text("\r\n".join(out_lines) + "\r\n", encoding="utf-8")

    after = before + len(fresh)
    cooldown_days = 45
    print(f"keywords.csv: {before:,} -> {after:,} rows (+{len(fresh):,} new)")
    print(f"at 6 posts/day the pool now covers {after / (6 * 365):.1f} years")
    print(f"at 3 posts/day the pool now covers {after / (3 * 365):.1f} years")
    print(f"unique subjects before the {cooldown_days}-day cooldown forces a repeat: {after:,}")
    return 0


def _csv_line(cells: list[str]) -> str:
    """Quote only when needed, matching the existing file's plain style."""
    out = []
    for c in cells:
        c = str(c)
        if any(ch in c for ch in (',', '"', '\n')):
            c = '"' + c.replace('"', '""') + '"'
        out.append(c)
    return ",".join(out)


if __name__ == "__main__":
    raise SystemExit(main())
