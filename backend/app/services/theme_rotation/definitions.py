"""What each Emerging Frontiers theme IS — the yardstick the monthly rotation scores against.

One definition per `trending_themes.slug`. A slug with no definition here is copied
unchanged every month (a new theme added in Studio is never rotated until someone writes
its definition). Bump `DEFINITIONS_VERSION` whenever a definition changes: the AI fit
verdicts are cached per version, so an edited definition is re-judged, not served stale.

* `seed_etfs` — liquid thematic ETFs whose holdings (FMP `etf/holdings`, entitled) seed the
  candidate pool and vote on relevance. Index providers' methodologies are the model here:
  a company several thematic funds hold is on-theme by the market's own judgement.
* `industries` — EXACT FMP `industry` strings (checked against the screener's industry
  list, 2026-09-23). A broad industry ("Software - Infrastructure") admits candidates but
  earns at most half credit; the description and revenue segments must do the rest.
* `segment_keywords` — matched against revenue-segment names to measure how much of a
  company's revenue is the theme (the MSCI "direct" method).
* `description_keywords` — DISTINCT phrases in the company's own business description;
  two or more are needed before the description earns any credit (the MSCI "indirect"
  method), which keeps a company that merely mentions a buzzword out.

Keywords match as WHOLE WORDS with a simple plural/verb ending (`scoring.keyword_pattern`):
"space" must not hit "aerospace", "gene" must not hit "general", "security" must not hit
"securities". So every form that matters is listed explicitly ("robot", "robotic",
"robotics").

"The New Oil" is critical minerals — rare earths, lithium, copper, uranium — by owner
decision (2026-09-23); its category was re-labelled in migration 174.
"""
from __future__ import annotations

from typing import Dict

from app.services.theme_rotation.models import ThemeDefinition

DEFINITIONS_VERSION = "2026-09-23.4"

_DEFINITIONS = (
    ThemeDefinition(
        slug="silicon-rush",
        label="AI and semiconductors: chips, chip equipment, chip design software and the "
              "data-centre hardware that AI runs on",
        seed_etfs=("SMH", "SOXX", "PSI"),
        industries=frozenset({"Semiconductors"}),
        segment_keywords=("semiconductor", "chip", "data center", "datacenter", "gpu",
                          "processor", "memory", "dram", "nand", "foundry", "wafer",
                          "analog", "networking", "compute", "computing", "accelerator",
                          "optical", "optical communications", "photonics", "laser",
                          "design automation", "semiconductor ip"),
        # AI-era hardware beyond the chip itself — optical interconnects (Lumentum, Coherent,
        # Credo) and chip-design software (Synopsys, Cadence) are in the curated list and were
        # nearly invisible to the first keyword set.
        description_keywords=("semiconductor", "integrated circuit", "chip", "gpu", "processor",
                              "wafer", "foundry", "memory", "data center", "artificial intelligence",
                              "accelerator", "electronic design automation", "optical interconnect",
                              "lithography", "optical", "photonic", "photonics", "laser",
                              "interconnect", "silicon ip", "chip design"),
    ),
    ThemeDefinition(
        slug="modern-battlefield",
        label="defense and military technology: weapons systems, munitions, military aircraft "
              "and ships, drones and defense electronics",
        seed_etfs=("ITA", "XAR", "SHLD"),
        industries=frozenset({"Aerospace & Defense"}),
        segment_keywords=("defense", "defence", "missile", "military", "munition", "weapon",
                          "combat", "naval", "shipbuilding", "mission systems", "aeronautics",
                          "space and airborne", "intelligence"),
        description_keywords=("defense", "military", "missile", "munition", "warfare",
                              "national security", "armed forces", "department of defense",
                              "combat", "weapon", "unmanned", "drone", "hypersonic", "naval",
                              "shipbuilding"),
    ),
    ThemeDefinition(
        slug="the-new-oil",
        label="critical minerals: miners and processors of rare earths, lithium, copper and "
              "uranium",
        seed_etfs=("REMX", "LIT", "COPX", "URNM"),
        industries=frozenset({"Industrial Materials", "Copper", "Uranium", "Chemicals - Specialty",
                              "Other Precious Metals"}),
        segment_keywords=("lithium", "copper", "uranium", "rare earth", "nickel", "cobalt",
                          "graphite", "magnet", "vanadium", "antimony", "tungsten", "manganese"),
        description_keywords=("lithium", "copper", "uranium", "rare earth", "critical mineral",
                              "nickel", "cobalt", "graphite", "vanadium", "antimony", "tungsten",
                              "manganese", "magnet"),
        small_cap=True,
    ),
    ThemeDefinition(
        slug="robot-workforce",
        label="robotics and automation: industrial and surgical robots, autonomous machines, "
              "machine vision and motion control",
        seed_etfs=("BOTZ", "ROBO", "ARKQ"),
        industries=frozenset({"Industrial - Machinery", "Computer Hardware",
                              "Medical - Instruments & Supplies", "Medical - Devices",
                              "Electrical Equipment & Parts"}),
        segment_keywords=("robot", "robotic", "robotics", "automation", "motion",
                          "machine vision", "autonomous", "surgical system"),
        description_keywords=("robot", "robotic", "robotics", "automation", "autonomous",
                              "machine vision", "motion control", "humanoid",
                              "industrial automation", "cobot", "surgical system"),
        # Small-cap floors: the curated list holds robotics pure plays well under $1B.
        small_cap=True,
    ),
    ThemeDefinition(
        slug="hacking-health",
        label="biotech and pharmaceuticals: drug makers, gene and cell therapies, and the tools "
              "that read and edit the genome",
        # PPH (large pharma, incl. ADRs) balances the biotech-heavy XBI/IBB/ARKG: without it
        # Novo Nordisk, AstraZeneca and Novartis had no fund votes at all.
        seed_etfs=("XBI", "IBB", "ARKG", "PPH"),
        industries=frozenset({"Biotechnology", "Drug Manufacturers - General",
                              "Drug Manufacturers - Specialty & Generic",
                              "Medical - Diagnostics & Research"}),
        segment_keywords=("pharmaceutical", "biopharma", "oncology", "immunology", "neuroscience",
                          "vaccine", "gene therapy", "gene therapies", "genetic", "genomic",
                          "genomics", "cell therapy", "cell therapies", "diabetes", "obesity",
                          "rare disease", "therapeutic",
                          "drug", "biologic", "sequencing"),
        description_keywords=("biotechnology", "biopharmaceutical", "pharmaceutical", "therapeutic",
                              "gene editing", "gene therapy", "gene therapies", "cell therapy",
                              "cell therapies", "crispr", "mrna",
                              "clinical-stage", "oncology", "genomic", "genomics", "sequencing",
                              "obesity", "glp-1"),
    ),
    ThemeDefinition(
        slug="cyber-wars",
        label="cybersecurity: software and services that protect networks, identities, "
              "endpoints, cloud workloads and data from attack",
        seed_etfs=("CIBR", "HACK", "BUG"),
        industries=frozenset({"Software - Infrastructure", "Software - Application"}),
        segment_keywords=("security", "cybersecurity", "cyber", "firewall", "identity",
                          "endpoint", "threat", "zero trust", "sase"),
        description_keywords=("cybersecurity", "cyber", "security platform", "threat", "firewall",
                              "zero trust", "identity security", "endpoint", "ransomware", "breach",
                              "vulnerability", "secure access"),
    ),
    ThemeDefinition(
        slug="powering-machine",
        label="the power behind the AI boom: nuclear power and fuel, reactors, power "
              "producers selling to data centres, and the grid, turbine and cooling equipment "
              "that generates, moves and cools their electricity",
        seed_etfs=("NLR", "GRID", "ZAP"),
        industries=frozenset({"Independent Power Producers", "Renewable Utilities",
                              "Regulated Electric", "Diversified Utilities",
                              "Electrical Equipment & Parts", "Engineering & Construction",
                              "Uranium"}),
        # SPECIFIC terms only. "power", "generation", "electric" and "transmission" match
        # every regulated utility's segment names ("Transmission and Distribution
        # Utilities", "Generation & Marketing"), and the first live preview (2026-09-23)
        # ranked eight regional utilities above the nuclear pure plays because of them.
        segment_keywords=("nuclear", "data center", "datacenter", "grid", "turbine", "cooling",
                          "electrification", "uranium", "enrichment", "reactor", "switchgear",
                          "power quality"),
        description_keywords=("nuclear", "reactor", "small modular", "uranium enrichment",
                              "data center", "gas turbine", "electrification", "grid infrastructure",
                              "power grid", "megawatt", "liquid cooling", "switchgear"),
        small_cap=True,
    ),
    ThemeDefinition(
        slug="final-frontier",
        label="space and satellites: launch, spacecraft, satellite communications and Earth "
              "observation",
        seed_etfs=("UFO", "ARKX", "ROKT"),
        industries=frozenset({"Aerospace & Defense", "Communication Equipment",
                              "Telecommunications Services"}),
        segment_keywords=("space", "satellite", "launch", "spacecraft", "lunar", "orbit",
                          "orbital", "earth observation"),
        description_keywords=("space", "satellite", "launch vehicle", "rocket", "spacecraft",
                              "orbit", "orbital", "lunar", "earth observation", "constellation",
                              "space-based"),
        small_cap=True,
    ),
)

THEME_DEFINITIONS: Dict[str, ThemeDefinition] = {d.slug: d for d in _DEFINITIONS}
