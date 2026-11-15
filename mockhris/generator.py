"""Synthetic HRIS dataset for Halcyon Systems Group (a fictitious software conglomerate).

`generate_dataset(seed)` returns (users, groups): HiBob-style employee records and
group definitions (static members and/or dynamic queries). Deterministic per seed
for a given run date (dates are relative to today).
"""
import json
import random
import re
import unicodedata
from datetime import date, timedelta

TODAY = date.today()
COMPANY_ID = 640321
PRIMARY_DOMAIN = "halcyon.example"

# --------------------------------------------------------------------------- #
# Geography and legal entities
# --------------------------------------------------------------------------- #
# site -> (country code, state, city, timezone)
SITES = {
    "Sydney":        ("AU", "NSW", "Sydney", "Australia/Sydney"),
    "Melbourne":     ("AU", "VIC", "Melbourne", "Australia/Melbourne"),
    "Singapore":     ("SG", None, "Singapore", "Asia/Singapore"),
    "Tokyo":         ("JP", "Tokyo", "Tokyo", "Asia/Tokyo"),
    "Bengaluru":     ("IN", "Karnataka", "Bengaluru", "Asia/Kolkata"),
    "London":        ("GB", "England", "London", "Europe/London"),
    "Remote - UK":   ("GB", None, None, "Europe/London"),
    "Dublin":        ("IE", "Leinster", "Dublin", "Europe/Dublin"),
    "Berlin":        ("DE", "Berlin", "Berlin", "Europe/Berlin"),
    "Remote - EU":   ("NL", None, None, "Europe/Amsterdam"),
    "San Francisco": ("US", "CA", "San Francisco", "America/Los_Angeles"),
    "New York":      ("US", "NY", "New York", "America/New_York"),
    "Austin":        ("US", "TX", "Austin", "America/Chicago"),
    "Remote - US":   ("US", None, None, "America/Denver"),
    "Toronto":       ("CA", "ON", "Toronto", "America/Toronto"),
}
SITE_IDS = {name: 201 + i for i, name in enumerate(SITES)}

COUNTRIES = {
    # code: (country name, region, Halcyon operating entity)
    "AU": ("Australia", "APAC", "Halcyon Systems Group Ltd"),
    "SG": ("Singapore", "APAC", "Halcyon Systems Asia Pte. Ltd."),
    "JP": ("Japan", "APAC", "Halcyon Systems Japan K.K."),
    "IN": ("India", "APAC", "Halcyon Systems India Pvt. Ltd."),
    "GB": ("United Kingdom", "EMEA", "Halcyon Systems UK Ltd"),
    "IE": ("Ireland", "EMEA", "Halcyon Systems Ireland Ltd"),
    "DE": ("Germany", "EMEA", "Halcyon Systems GmbH"),
    "NL": ("Netherlands", "EMEA", "Employer of Record (Netherlands)"),
    "US": ("United States", "AMER", "Halcyon Systems Inc."),
    "CA": ("Canada", "AMER", "Halcyon Systems Canada Inc."),
}

# Acquired subsidiaries keep their own legal entity and email domain.
SUBSIDIARIES = {
    "Ferrocene Labs":      ("Ferrocene Labs GmbH", "ferrocene.example"),
    "Quillmark Analytics": ("Quillmark Analytics Inc.", "quillmark.example"),
    "Bluegate Security":   ("Bluegate Security Ltd", "bluegate.example"),
}

# --------------------------------------------------------------------------- #
# Organisation design
#   division: (head title, head site, reports-to division or None, [departments])
#   department: (name, head title or None, head site, [teams])
#   team: (name, manager title or None, [IC title roots], IC count, [sites])
# --------------------------------------------------------------------------- #
ORG = {
    "Executive Office": ("Chief Executive Officer", "Sydney", None, [
        ("Office of the CEO", None, None, [
            ("Office of the CEO", "Chief of Staff", ["Executive Business Partner", "Strategy & Operations Analyst"], 3, ["Sydney", "San Francisco"]),
        ]),
    ]),
    "Engineering": ("Chief Technology Officer", "San Francisco", "Executive Office", [
        ("Platform Engineering", "VP, Platform Engineering", "Melbourne", [
            ("Compute & Orchestration", None, ["Software Engineer", "Site Reliability Engineer"], 6, ["Melbourne", "Sydney", "Bengaluru"]),
            ("Developer Experience", None, ["Software Engineer", "Build Engineer"], 5, ["Melbourne", "Remote - US"]),
            ("CI/CD & Release", None, ["Release Engineer", "Software Engineer"], 5, ["Bengaluru", "Melbourne"]),
            ("Observability", None, ["Software Engineer", "Site Reliability Engineer"], 5, ["Berlin", "London"]),
            ("Site Reliability", None, ["Site Reliability Engineer"], 7, ["Sydney", "Austin", "Dublin"]),
            ("Cloud Infrastructure", None, ["Cloud Engineer", "Network Engineer"], 5, ["Austin", "Bengaluru"]),
        ]),
        ("Product Engineering", "VP, Product Engineering", "San Francisco", [
            ("Identity & Access", None, ["Software Engineer"], 6, ["San Francisco", "Austin"]),
            ("Payments", None, ["Software Engineer", "QA Engineer"], 7, ["New York", "London"]),
            ("Core API", None, ["Software Engineer"], 7, ["San Francisco", "Bengaluru"]),
            ("Web Experience", None, ["Frontend Engineer", "QA Engineer"], 6, ["Sydney", "Melbourne"]),
            ("Mobile", None, ["iOS Engineer", "Android Engineer"], 6, ["London", "Berlin"]),
            ("Integrations & Ecosystem", None, ["Software Engineer"], 5, ["Dublin", "Remote - UK"]),
        ]),
        ("Data & AI", "VP, Data & AI", "London", [
            ("Data Platform", None, ["Data Engineer"], 6, ["London", "Bengaluru"]),
            ("Machine Learning", None, ["Machine Learning Engineer", "Research Scientist"], 6, ["London", "San Francisco"]),
            ("Applied AI", None, ["Applied AI Engineer", "Software Engineer"], 5, ["San Francisco", "Tokyo"]),
        ]),
        ("Architecture Office", None, None, [
            ("Architecture", "Chief Architect", ["Software Architect"], 3, ["Sydney", "San Francisco", "Berlin"]),
        ]),
    ]),
    "Security": ("Chief Information Security Officer", "London", "Executive Office", [
        ("Security Engineering", "Director, Security Engineering", "London", [
            ("Application Security", None, ["Application Security Engineer"], 4, ["London", "Bengaluru"]),
            ("Cloud Security", None, ["Cloud Security Engineer"], 4, ["Sydney", "Austin"]),
        ]),
        ("Security Operations", "Director, Security Operations", "Dublin", [
            ("Detection & Response", None, ["Security Analyst", "Detection Engineer"], 6, ["Dublin", "Singapore", "Austin"]),
            ("Threat & Vulnerability Management", None, ["Vulnerability Analyst"], 3, ["Bengaluru", "Dublin"]),
        ]),
    ]),
    "Information Technology": ("Chief Information Officer", "Sydney", "Executive Office", [
        ("Corporate IT", "Director, Corporate IT", "Sydney", [
            ("Identity & Access Management", None, ["Identity Engineer"], 4, ["Sydney", "Dublin"]),
            ("End User Computing", None, ["Endpoint Engineer", "IT Support Analyst"], 6, ["Sydney", "San Francisco", "London", "Bengaluru", "Singapore"]),
            ("Enterprise Applications", None, ["Business Systems Analyst", "Salesforce Developer"], 5, ["Austin", "Bengaluru"]),
        ]),
    ]),
    "Product": ("Chief Product Officer", "San Francisco", "Executive Office", [
        ("Product Management", "VP, Product Management", "San Francisco", [
            ("Platform Product", "Group Product Manager, Platform", ["Product Manager"], 3, ["San Francisco", "Melbourne"]),
            ("Core Product", "Group Product Manager, Core", ["Product Manager"], 4, ["San Francisco", "London"]),
            ("Growth", "Group Product Manager, Growth", ["Product Manager", "Product Analyst"], 4, ["New York"]),
        ]),
        ("Design & Research", "Director, Design", "Sydney", [
            ("Product Design", "Design Manager", ["Product Designer"], 6, ["Sydney", "Berlin", "San Francisco"]),
            ("User Research", "Research Manager", ["UX Researcher"], 3, ["London"]),
            ("Design Systems", "Design Systems Lead", ["Design Technologist"], 3, ["Melbourne"]),
        ]),
        ("Documentation", None, None, [
            ("Technical Writing", "Documentation Manager", ["Technical Writer"], 4, ["Remote - UK", "Bengaluru"]),
        ]),
    ]),
    "Revenue": ("Chief Revenue Officer", "New York", "Executive Office", [
        ("Sales", "VP, Global Sales", "New York", [
            ("Enterprise Sales AMER", "Regional Sales Director, AMER", ["Account Executive", "Sales Development Representative"], 7, ["New York", "San Francisco", "Austin", "Toronto"]),
            ("Enterprise Sales EMEA", "Regional Sales Director, EMEA", ["Account Executive", "Sales Development Representative"], 6, ["London", "Dublin", "Berlin"]),
            ("Enterprise Sales APAC", "Regional Sales Director, APAC", ["Account Executive", "Sales Development Representative"], 5, ["Sydney", "Singapore", "Tokyo"]),
            ("Solutions Engineering", "Director, Solutions Engineering", ["Solutions Engineer"], 6, ["New York", "London", "Sydney"]),
            ("Sales Operations", "Sales Operations Manager", ["Sales Operations Analyst", "Deal Desk Specialist"], 4, ["Austin", "Dublin"]),
        ]),
        ("Customer Success", "VP, Customer Success", "Dublin", [
            ("Technical Support", "Support Manager", ["Support Engineer"], 8, ["Dublin", "Austin", "Singapore", "Bengaluru"]),
            ("Customer Success Management", "Customer Success Director", ["Customer Success Manager"], 5, ["New York", "London", "Sydney"]),
            ("Professional Services", "Professional Services Manager", ["Implementation Consultant"], 4, ["Toronto", "Melbourne"]),
        ]),
    ]),
    "Marketing": ("Chief Marketing Officer", "New York", "Executive Office", [
        ("Brand & Communications", "Director, Brand & Communications", "New York", [
            ("Brand & Creative", "Creative Director", ["Brand Designer", "Copywriter"], 4, ["New York", "London"]),
            ("Corporate Communications", "Communications Manager", ["Communications Specialist", "Social Media Specialist"], 3, ["Sydney", "New York"]),
        ]),
        ("Product Marketing", "Director, Product Marketing", "San Francisco", [
            ("Product Marketing", "Product Marketing Lead", ["Product Marketing Manager"], 4, ["San Francisco", "London"]),
        ]),
        ("Demand Generation", "Director, Demand Generation", "Austin", [
            ("Demand Gen AMER", "Demand Generation Manager, AMER", ["Campaign Manager", "Marketing Operations Specialist"], 4, ["Austin"]),
            ("Demand Gen International", "Demand Generation Manager, International", ["Campaign Manager"], 3, ["London", "Singapore"]),
        ]),
        ("Developer Relations", None, None, [
            ("Developer Relations", "Head of Developer Relations", ["Developer Advocate"], 4, ["Berlin", "San Francisco", "Tokyo"]),
        ]),
    ]),
    "Finance": ("Chief Financial Officer", "Sydney", "Executive Office", [
        ("Financial Planning & Analysis", None, None, [
            ("FP&A", "FP&A Director", ["Financial Analyst"], 4, ["Sydney", "New York"]),
        ]),
        ("Accounting", "Group Financial Controller", "Sydney", [
            ("General Ledger", "Accounting Manager", ["Accountant"], 4, ["Sydney", "Bengaluru"]),
            ("Payables & Receivables", "AP/AR Manager", ["Accounts Payable Specialist", "Billing Specialist"], 4, ["Bengaluru", "Dublin"]),
            ("Tax", "Head of Tax", ["Tax Analyst"], 3, ["Sydney", "Dublin", "New York"]),
        ]),
        ("Treasury & Procurement", "Director, Treasury & Procurement", "Sydney", [
            ("Treasury", "Treasury Manager", ["Treasury Analyst"], 2, ["Sydney"]),
            ("Procurement", "Procurement Manager", ["Procurement Specialist"], 3, ["Austin", "London"]),
        ]),
    ]),
    "Legal & Compliance": ("General Counsel", "Sydney", "Executive Office", [
        ("Legal", "Deputy General Counsel", "London", [
            ("Commercial Legal", "Associate General Counsel, Commercial", ["Legal Counsel", "Contracts Manager", "Paralegal"], 5, ["London", "New York", "Sydney"]),
            ("Corporate & Securities", "Associate General Counsel, Corporate", ["Legal Counsel", "Company Secretary"], 3, ["Sydney"]),
            ("Intellectual Property", "Head of IP", ["IP Counsel", "Paralegal"], 2, ["Berlin", "San Francisco"]),
            ("Employment Law", "Head of Employment Law", ["Employment Counsel"], 2, ["London", "Austin"]),
        ]),
        ("Compliance & Risk", "Chief Compliance Officer", "Dublin", [
            ("Privacy & Data Protection", "Data Protection Officer", ["Privacy Counsel", "Privacy Analyst"], 3, ["Dublin", "Berlin"]),
            ("Governance, Risk & Compliance", "GRC Manager", ["GRC Analyst", "Compliance Analyst"], 5, ["Dublin", "Austin", "Singapore"]),
            ("Internal Audit", "Head of Internal Audit", ["Internal Auditor"], 3, ["Sydney", "London"]),
        ]),
    ]),
    "People": ("Chief People Officer", "Melbourne", "Executive Office", [
        ("Talent Acquisition", "Director, Talent Acquisition", "Melbourne", [
            ("Technical Recruiting", "Technical Recruiting Manager", ["Technical Recruiter", "Recruiting Coordinator"], 6, ["Melbourne", "San Francisco", "London", "Bengaluru"]),
            ("GTM Recruiting", "GTM Recruiting Manager", ["Recruiter"], 3, ["New York", "Dublin"]),
        ]),
        ("People Partnering", None, None, [
            ("People Partners", "Head of People Partnering", ["People Partner"], 5, ["Melbourne", "San Francisco", "London", "Singapore", "Berlin"]),
        ]),
        ("People Operations", "Director, People Operations", "Melbourne", [
            ("People Operations", "People Operations Manager", ["People Operations Specialist", "HRIS Analyst"], 4, ["Melbourne", "Austin", "Dublin"]),
            ("Total Rewards", "Total Rewards Manager", ["Compensation Analyst", "Benefits Specialist"], 3, ["San Francisco", "Melbourne"]),
            ("Learning & Development", "L&D Manager", ["Learning Partner"], 3, ["London"]),
        ]),
    ]),
    "Operations": ("Chief Operating Officer", "Sydney", "Executive Office", [
        ("Workplace", None, None, [
            ("Workplace & Facilities", "Global Workplace Manager", ["Workplace Coordinator", "Facilities Manager"], 6, ["Sydney", "San Francisco", "New York", "London", "Berlin", "Bengaluru", "Singapore", "Tokyo"]),
        ]),
        ("Strategy & Corporate Development", "VP, Strategy & Corporate Development", "New York", [
            ("Business Operations", "Business Operations Manager", ["Business Operations Analyst"], 3, ["Sydney", "San Francisco"]),
            ("Corporate Development", "Director, Corporate Development", ["Corporate Development Analyst"], 2, ["New York"]),
        ]),
    ]),
    # ---- acquired subsidiaries (own legal entity + email domain) ----
    "Ferrocene Labs": ("General Manager, Ferrocene Labs", "Berlin", "Engineering", [
        ("Ferrocene Engineering", "Head of Engineering, Ferrocene", "Berlin", [
            ("Compiler Toolchain", None, ["Compiler Engineer"], 5, ["Berlin"]),
            ("IDE Integrations", None, ["Software Engineer"], 4, ["Berlin", "Remote - EU"]),
        ]),
        ("Ferrocene Community", None, None, [
            ("Ferrocene Developer Community", "Community Lead", ["Developer Advocate", "Community Manager"], 3, ["Berlin", "Remote - EU"]),
        ]),
    ]),
    "Quillmark Analytics": ("General Manager, Quillmark Analytics", "Toronto", "Product", [
        ("Quillmark Engineering", "Head of Engineering, Quillmark", "Toronto", [
            ("Analytics Engine", None, ["Software Engineer", "Data Engineer"], 5, ["Toronto"]),
            ("Visualisation", None, ["Frontend Engineer"], 4, ["Toronto", "Remote - US"]),
        ]),
        ("Quillmark Customer Engineering", None, None, [
            ("Quillmark Solutions", "Solutions Lead, Quillmark", ["Solutions Engineer"], 3, ["Toronto", "New York"]),
        ]),
    ]),
    "Bluegate Security": ("General Manager, Bluegate Security", "London", "Security", [
        ("Bluegate Research", "Head of Research, Bluegate", "London", [
            ("Threat Intelligence", None, ["Threat Intelligence Analyst"], 4, ["London", "Remote - UK"]),
            ("Endpoint Agent", None, ["Software Engineer"], 5, ["London", "Remote - UK"]),
        ]),
    ]),
}

# divisions whose teams are engineering teams (manager title + Staff levels)
ENGINEERING_DIVISIONS = {"Engineering", "Ferrocene Labs", "Quillmark Analytics", "Bluegate Security"}
EXECUTIVE_LEVEL = {"Executive Office": "E1"}

LEVELS = [("IC1", "Associate "), ("IC2", ""), ("IC3", "Senior "), ("IC4", None), ("IC5", "Principal ")]
LEVEL_WEIGHTS = [12, 35, 32, 15, 6]
TECH_WORDS = ("Engineer", "Scientist", "Developer", "Architect", "Technologist")

# --------------------------------------------------------------------------- #
# Names (some include diacritics, apostrophes, hyphens and spaces on purpose,
# to exercise email/userName normalisation in provisioning).
# --------------------------------------------------------------------------- #
NAMES = {
    "anglo": (
        ["Olivia", "James", "Charlotte", "William", "Amelia", "Jack", "Isla", "Henry", "Grace", "Thomas",
         "Matilda", "Lachlan", "Harper", "Oliver", "Evie", "Samuel", "Ruby", "Lucas", "Chloe", "Ethan",
         "Sophie", "Noah", "Hannah", "Daniel", "Emily", "Benjamin", "Zoë", "Liam", "Madison", "Caleb",
         "Abigail", "Nathan", "Georgia", "Riley", "Eleanor", "Hugo", "Freya", "Oscar", "Imogen", "Jordan"],
        ["Smith", "Walsh", "Taylor", "Brown", "Wilson", "Clarke", "Hughes", "Bennett", "Fraser", "Campbell",
         "O'Sullivan", "Mitchell", "Harrison", "Reid", "Morgan", "Hart", "Baxter", "Cooper", "Kelly", "Doyle",
         "Fitzgerald", "Hamilton", "Sinclair", "Whitfield", "Ashworth", "Smith-Jones", "Lawson", "Prescott",
         "Thornton", "Ellis", "Marsh", "Holloway", "Pemberton", "Caldwell", "Rhodes", "Barker", "Fletcher", "Gale"],
    ),
    "irish": (
        ["Siobhán", "Cian", "Aoife", "Oisín", "Niamh", "Ciarán", "Sinéad", "Darragh", "Róisín", "Eoin",
         "Clodagh", "Seán", "Saoirse", "Pádraig", "Caoimhe", "Ronan"],
        ["Murphy", "O'Brien", "Ní Bhriain", "Kennedy", "Ó Súilleabháin", "Brennan", "Quinn", "McCarthy",
         "Nolan", "Duffy", "Byrne", "Gallagher", "Ó Riain", "Kavanagh"],
    ),
    "german": (
        ["Jürgen", "Lena", "Maximilian", "Anja", "Felix", "Katrin", "Jonas", "Sophie", "Lukas", "Mareike",
         "Tobias", "Hanna", "Niklas", "Franziska", "Moritz", "Jana"],
        ["Müller", "Schröder", "Becker", "Hoffmann", "Wagner", "Koch", "Weiß", "Krüger", "Zimmermann",
         "Braun", "von Holt", "Schäfer", "Neumann", "Lehmann", "Groß"],
    ),
    "dutch": (
        ["Daan", "Fleur", "Sem", "Noor", "Bram", "Lotte", "Jesse", "Sanne", "Thijs", "Femke"],
        ["de Vries", "Jansen", "van Dijk", "Bakker", "Visser", "Smit", "van den Berg", "Mulder", "de Jong"],
    ),
    "indian": (
        ["Aarav", "Priya", "Vihaan", "Ananya", "Arjun", "Diya", "Rohan", "Kavya", "Siddharth", "Meera",
         "Karthik", "Lakshmi", "Aditya", "Shreya", "Varun", "Nikhil", "Pooja", "Rahul"],
        ["Sharma", "Iyer", "Reddy", "Nair", "Patel", "Gupta", "Menon", "Rao", "Krishnan", "Banerjee",
         "Venkatesh", "Kulkarni", "Desai", "Chatterjee", "Pillai", "Subramanian"],
    ),
    "chinese": (
        ["Wei Ling", "Jun Jie", "Hui Min", "Mei Xin", "Zhi Hao", "Xin Yi", "Jia Hui", "Kai Wen", "Yan Ting", "Hao Ran"],
        ["Tan", "Lim", "Ng", "Wong", "Goh", "Chua", "Teo", "Chen", "Wang", "Zhang", "Liu", "Huang"],
    ),
    "malay": (
        ["Nur Aisyah", "Irfan", "Siti", "Hafiz", "Farah", "Amirul", "Aqilah", "Danial"],
        ["Rahman", "Ismail", "Abdullah", "Hassan", "bin Osman", "binti Yusof", "Ibrahim"],
    ),
    "japanese": (
        ["Haruto", "Yui", "Sota", "Aoi", "Ren", "Hina", "Takumi", "Sakura", "Kenji", "Yuka", "Daiki", "Mio"],
        ["Sato", "Suzuki", "Takahashi", "Tanaka", "Watanabe", "Ito", "Yamamoto", "Nakamura", "Kobayashi", "Kato"],
    ),
    "korean": (
        ["Ji-woo", "Min-jun", "Seo-yeon", "Do-hyun", "Ha-eun", "Joon", "Yuna", "Hyun-woo"],
        ["Kim", "Park", "Choi", "Jung", "Kang", "Yoon", "Jang", "Lim"],
    ),
    "hispanic": (
        ["Mateo", "Sofía", "José", "Camila", "Tomás", "Valentina", "Diego", "Lucía", "Alejandro", "Ximena", "Santiago", "Paula"],
        ["García", "Rodríguez", "Fernández", "López", "Martínez", "Hernández", "Gómez", "Ramírez", "Castillo", "Ortega"],
    ),
    "portuguese": (
        ["João", "Beatriz", "Gonçalo", "Mariana", "Rafael", "Inês", "Thiago", "Larissa"],
        ["Ferreira", "Silva", "Santos", "Oliveira", "Costa", "Gonçalves", "Pereira", "Almeida"],
    ),
    "west_african": (
        ["Kwame", "Amara", "Oluwaseun", "Chiamaka", "Kofi", "Adaeze", "Tunde", "Ngozi", "Yaw", "Folake"],
        ["Okafor", "Mensah", "Adeyemi", "Boateng", "Okonkwo", "Asante", "Balogun", "Eze", "Owusu"],
    ),
    "arabic": (
        ["Ahmed", "Fatima", "Hamid", "Leila", "Yusuf", "Mariam", "Omar", "Noor", "Karim", "Rania"],
        ["Hussain", "Khan", "Haddad", "Al-Sayed", "Mansour", "Nasser", "Farouk", "Khalil", "El-Amin"],
    ),
    "polish": (
        ["Agnieszka", "Piotr", "Katarzyna", "Tomasz", "Magdalena", "Paweł", "Zofia", "Michał"],
        ["Kowalski", "Nowak", "Wiśniewska", "Wójcik", "Lewandowski", "Kamińska", "Zieliński"],
    ),
    "nordic": (
        ["Björn", "Søren", "Ingrid", "Astrid", "Lars", "Sigrid", "Magnus", "Freja", "Erik", "Linnea"],
        ["Lindqvist", "Sørensen", "Johansson", "Nilsen", "Andersen", "Bergström", "Halvorsen", "Ødegaard"],
    ),
    "italian": (
        ["Luca", "Giulia", "Matteo", "Francesca", "Alessandro", "Chiara", "Lorenzo", "Elena"],
        ["Rossi", "Bianchi", "Romano", "Colombo", "Ricci", "Marino", "Greco", "De Luca"],
    ),
    "vietnamese": (
        ["Minh", "Linh", "Duc", "Thao", "Quang", "Hoa", "Tuan", "Lan"],
        ["Nguyen", "Tran", "Le", "Pham", "Hoang", "Vu", "Dang"],
    ),
    "french": (
        ["Camille", "Hugo", "Léa", "Théo", "Manon", "Antoine", "Chloé", "Étienne", "Margaux", "Julien"],
        ["Dubois", "Lefèvre", "Moreau", "Laurent", "Girard", "Rousseau", "Fontaine", "Mercier"],
    ),
}
DIASPORA = ["chinese", "indian", "hispanic", "korean", "vietnamese", "west_african", "arabic", "italian",
            "polish", "portuguese", "french", "nordic", "irish", "japanese"]
# country -> [(pool, weight)]; "diaspora" picks any pool from DIASPORA
NAME_POOLS_BY_COUNTRY = {
    "AU": [("anglo", 55), ("diaspora", 45)],
    "US": [("anglo", 50), ("diaspora", 50)],
    "CA": [("anglo", 50), ("diaspora", 40), ("french", 10)],
    "GB": [("anglo", 60), ("diaspora", 40)],
    "IE": [("irish", 70), ("anglo", 15), ("diaspora", 15)],
    "DE": [("german", 75), ("diaspora", 25)],
    "NL": [("dutch", 70), ("diaspora", 30)],
    "IN": [("indian", 100)],
    "SG": [("chinese", 65), ("malay", 20), ("indian", 15)],
    "JP": [("japanese", 100)],
}

GROUP_DISPLAY_NAMES = {
    "all-staff": "All Staff", "employees": "Employees", "contractors": "Contractors", "interns": "Interns",
    "people-managers": "People Managers", "executive-leadership": "Executive Leadership",
    "senior-leadership": "Senior Leadership", "subsidiary-staff": "Subsidiary Staff",
    "engineering-org": "Engineering Org", "github-emu-users": "GitHub EMU Users",
    "github-copilot": "GitHub Copilot", "github-enterprise-owners": "GitHub Enterprise Owners",
    "emea-engineering": "EMEA Engineering", "soc2-production-access": "SOC 2 Production Access",
    "incident-commanders": "Incident Commanders", "security-champions": "Security Champions",
    "legal-and-compliance-reviewers": "Legal & Compliance Reviewers",
    "change-advisory-board": "Change Advisory Board",
}

PRONOUNS = {"Male": "He/Him", "Female": "She/Her", "Non-binary": "They/Them"}
HOBBIES = ["Climbing", "Cycling", "Photography", "Board games", "Running", "Cooking", "Gardening", "Chess",
           "Surfing", "Woodworking", "Reading", "Yoga", "Music", "Hiking", "Pottery", "Football"]


def future_monday(today, offset_days):
    """The Monday on or before today + offset, pushed a week later if that isn't after today."""
    start = today + timedelta(days=offset_days)
    start -= timedelta(days=start.weekday())
    if start <= today:
        start += timedelta(days=7)
    return start


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def ascii_fold(text):
    text = text.replace("ß", "ss").replace("ø", "o").replace("Ø", "O").replace("æ", "ae")
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def email_local(first, last):
    clean = lambda s: re.sub(r"[^a-z0-9-]", "", ascii_fold(s).lower().replace(" ", ""))
    return f"{clean(first)}.{clean(last)}"


class Generator:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.people = []          # list of dicts (internal representation)
        self.used_emails = set()
        self.next_id = 3332883884017713000

    # -- helpers -------------------------------------------------------------
    def pick_name(self, country):
        pools, weights = zip(*NAME_POOLS_BY_COUNTRY[country])
        pool = self.rng.choices(pools, weights)[0]
        if pool == "diaspora":
            pool = self.rng.choice(DIASPORA)
        firsts, lasts = NAMES[pool]
        return self.rng.choice(firsts), self.rng.choice(lasts)

    def make_email(self, first, last, domain):
        base = email_local(first, last)
        candidate, n = f"{base}@{domain}", 1
        while candidate in self.used_emails:
            n += 1
            candidate = f"{base}{n}@{domain}"
        self.used_emails.add(candidate)
        return candidate

    def start_date(self, level):
        earliest = {"E": 2012, "M": 2014, "I": 2016}[level[0]]
        start = date(earliest, 1, 1)
        span = (TODAY - timedelta(days=30) - start).days
        d = start + timedelta(days=self.rng.randint(0, span))
        return d - timedelta(days=d.weekday())  # Mondays

    def add_person(self, *, title, level, division, department, team, site, manager, is_ic=False, eng=False):
        country = SITES[site][0]
        entity, domain = COUNTRIES[country][2], PRIMARY_DOMAIN
        if division in SUBSIDIARIES:
            entity, domain = SUBSIDIARIES[division]
        first, last = self.pick_name(country)
        # mostly avoid namesakes, but keep a few so the email suffix logic (jane.doe2@) is exercised
        for _ in range(25):
            if f"{email_local(first, last)}@{domain}" not in self.used_emails or self.rng.random() < 0.15:
                break
            first, last = self.pick_name(country)
        gender = self.rng.choices(["Female", "Male", "Non-binary"], [48, 48, 4])[0]

        employment, contract, lifecycle = "Permanent", "Full time", "employed"
        start = self.start_date(level)
        termination = None
        if is_ic:
            roll = self.rng.random()
            if eng and level == "IC1" and roll < 0.3:
                employment, contract = "Intern", "Fixed term"
                title = title.replace("Associate ", "") + " Intern"
                start = TODAY - timedelta(days=self.rng.randint(14, 150))
            elif roll < 0.07:
                employment = "Contractor"
            elif roll < 0.10:
                employment, contract = "Fixed Term", "Fixed term"
            if self.rng.random() < 0.05:
                contract = "Part time"
            roll = self.rng.random()
            if roll < 0.04:
                lifecycle = "terminated"
                termination = TODAY - timedelta(days=self.rng.randint(3, 160))
                if start >= termination:
                    start = termination - timedelta(days=self.rng.randint(200, 1500))
            elif roll < 0.065:
                lifecycle = "hired"
                start = future_monday(TODAY, self.rng.randint(5, 75))
            elif roll < 0.085:
                lifecycle = "leave"

        self.next_id += self.rng.randint(1000, 9000)
        p = dict(
            id=str(self.next_id), first=first, last=last, gender=gender,
            email=self.make_email(first, last, domain), title=title, level=level,
            division=division, department=department, team=team, site=site, country=country,
            entity=entity, manager=manager, start=start, lifecycle=lifecycle,
            termination=termination, employment=employment, contract=contract,
            employee_no=f"HSG-{10001 + len(self.people)}",
        )
        self.people.append(p)
        return p

    # -- org construction ----------------------------------------------------
    def build(self):
        heads = {}
        # executives first so subsidiaries can report to them
        order = sorted(ORG, key=lambda d: (ORG[d][2] is not None, ORG[d][2] in SUBSIDIARIES or d in SUBSIDIARIES))
        cc = 100
        for division in order:
            head_title, head_site, reports_to, departments = ORG[division]
            level = EXECUTIVE_LEVEL.get(division, "E2" if division not in SUBSIDIARIES else "E3")
            first_dept, first_team = departments[0][0], departments[0][3][0][0]
            div_head = self.add_person(
                title=head_title, level=level, division=division,
                department=first_dept if division == "Executive Office" else division,
                team=first_team if division == "Executive Office" else None, site=head_site,
                manager=heads.get(reports_to))
            heads[division] = div_head
            eng = division in ENGINEERING_DIVISIONS
            for dept, dept_head_title, dept_site, teams in departments:
                cc += 10
                dept_head = div_head
                if dept_head_title:
                    dept_head = self.add_person(
                        title=dept_head_title, level="M5" if dept_head_title.startswith("VP") else "M4",
                        division=division, department=dept, team=None, site=dept_site, manager=div_head)
                for team, mgr_title, roots, size, sites in teams:
                    if mgr_title is None:
                        mgr_title = f"Engineering Manager, {team}" if eng else f"Manager, {team}"
                    if division == "Executive Office":
                        mgr = self.add_person(title=mgr_title, level="M3", division=division, department=dept,
                                              team=team, site=sites[0], manager=div_head)
                    else:
                        mgr = self.add_person(title=mgr_title, level="M3" if "Director" in mgr_title or "Head" in mgr_title else "M2",
                                              division=division, department=dept, team=team, site=sites[0], manager=dept_head)
                    for i in range(size):
                        root = self.rng.choice(roots)
                        level, prefix = self.rng.choices(LEVELS, LEVEL_WEIGHTS)[0]
                        tech = any(w in root for w in TECH_WORDS)
                        if prefix is None:
                            prefix = "Staff " if tech else "Lead "
                        self.add_person(title=prefix + root, level=level, division=division, department=dept,
                                        team=team, site=sites[i % len(sites)], manager=mgr, is_ic=True, eng=tech)
                    for p in self.people:
                        if p["department"] == dept and p["division"] == division:
                            p["cost_centre"] = f"CC-{cc}"
        for p in self.people:
            p.setdefault("cost_centre", "CC-100")
            if p["team"] is None:  # leaders without a team belong to their department's leadership group
                p["team"] = f"{p['department']} Leadership"

    # -- HiBob-style serialisation ------------------------------------------
    def to_hibob(self, p, reports):
        country_name, region, _ = COUNTRIES[p["country"]]
        _, state, city, tz = SITES[p["site"]]
        mgr = p["manager"]
        second = mgr["manager"] if mgr else None
        ref = lambda m: m and {"id": m["id"], "displayName": f"{m['first']} {m['last']}", "email": m["email"]}
        direct = sorted(r["email"] for r in reports.get(p["id"], []))
        status = "Active" if p["lifecycle"] in ("employed", "leave") else "Inactive"
        rng = random.Random(p["id"])
        dob_year = rng.randint(1965, 2003)
        tenure = max(0, (TODAY - p["start"]).days // 365) if p["start"] <= TODAY else 0
        return {
            "id": p["id"],
            "companyId": COMPANY_ID,
            "email": p["email"],
            "firstName": p["first"],
            "surname": p["last"],
            "displayName": f"{p['first']} {p['last']}",
            "fullName": f"{p['first']} {p['last']}",
            "creationDateTime": f"{(p['start'] - timedelta(days=21)).isoformat()}T09:00:00.000000",
            "personal": {
                "honorific": None,
                "preferredName": None,
                "gender": p["gender"],
                "pronouns": PRONOUNS[p["gender"]],
                "shortBirthDate": f"{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
                "age": TODAY.year - dob_year,
                "nationality": [country_name],
                "communication": {"slackUsername": p["email"].split("@")[0], "skypeUsername": None},
            },
            "about": {
                "avatar": None,
                "hobbies": rng.sample(HOBBIES, rng.randint(0, 3)),
                "about": None,
                "socialData": {"linkedin": None, "twitter": None, "facebook": None},
            },
            "work": {
                "employeeIdInCompany": p["employee_no"],
                "title": p["title"],
                "department": p["department"],
                "site": p["site"],
                "siteId": SITE_IDS[p["site"]],
                "startDate": p["start"].isoformat(),
                "isManager": bool(direct),
                "reportsTo": ref(mgr),
                "secondLevelManager": second["id"] if second else None,
                "directReports": direct,
                "tenureYears": tenure,
                "workPhone": None,
                "custom": {
                    "division": p["division"],
                    "team": p["team"],
                    "jobLevel": p["level"],
                    "costCentre": p["cost_centre"],
                    "legalEntity": p["entity"],
                    "region": region,
                },
            },
            "payroll": {
                "employment": {
                    "type": p["employment"],
                    "contract": p["contract"],
                    "weeklyHours": 20 if p["contract"] == "Part time" else 38 if p["country"] == "AU" else 40,
                    "legalEntity": p["entity"],
                },
            },
            "address": {
                "country": country_name,
                "state": state,
                "city": city,
                "timezone": tz,
            },
            "internal": {
                "status": status,
                "lifecycleStatus": p["lifecycle"],
                "terminationDate": p["termination"].isoformat() if p["termination"] else None,
                "terminationReason": rng.choice(["Resignation", "Redundancy", "End of contract"]) if p["termination"] else None,
                "notice": None,
            },
        }

    # -- groups --------------------------------------------------------------
    def groups(self):
        out = []

        def g(name, description, query=None, members=None):
            if name.startswith(("div-", "dept-", "team-", "region-", "entity-")):
                kind = {"div": "Division", "dept": "Department", "team": "Team",
                        "region": "Region", "entity": "Entity"}[name.split("-")[0]]
                label = re.search(r"(?:division|department|team|region|legalEntity) == \"(.*)\"", query).group(1)
                display = f"{kind}: {label}"
            else:
                display = GROUP_DISPLAY_NAMES[name]
            doc = {"name": name, "displayName": display, "description": description}
            if members is not None:
                doc["members"] = sorted(members)
            if query is not None:
                doc["query"] = query
            out.append(doc)

        q = lambda s: json.dumps(s, ensure_ascii=False)  # quote a string literal for a query

        # --- org structure groups (mirror the HRIS) ---
        for division, (_, _, _, departments) in ORG.items():
            g(f"div-{slug(division)}", f"Everyone in the {division} division.", f"division == {q(division)}")
            for dept, _, _, teams in departments:
                g(f"dept-{slug(dept)}", f"Everyone in the {dept} department ({division}).", f"department == {q(dept)}")
                for team, *_ in teams:
                    g(f"team-{slug(team)}", f"The {team} team ({dept}).", f"team == {q(team)}")
        for region in ("APAC", "EMEA", "AMER"):
            g(f"region-{region.lower()}", f"Everyone based in {region}.", f"region == {q(region)}")
        entities = sorted({p["entity"] for p in self.people})
        for e in entities:
            g(f"entity-{slug(e)}", f"Everyone employed by {e}.", f"legalEntity == {q(e)}")

        # --- workforce groups ---
        g("all-staff", "All current workers (employed or on leave).", 'lifecycle in ["employed", "leave"]')
        g("employees", "Current permanent and fixed-term employees.",
          'memberOf("all-staff") and employmentType in ["Permanent", "Fixed Term"]')
        g("contractors", "Current contractors.", 'memberOf("all-staff") and employmentType == "Contractor"')
        g("interns", "Current interns.", 'memberOf("all-staff") and employmentType == "Intern"')
        g("people-managers", "Everyone with at least one direct report.", "isManager == true")
        g("executive-leadership", "CEO, C-suite and subsidiary general managers.", 'jobLevel in ["E1", "E2", "E3"]')
        g("senior-leadership", "Executives plus VPs and directors.",
          'memberOf("executive-leadership") or jobLevel in ["M5", "M4"]')
        g("subsidiary-staff", "Everyone employed by an acquired subsidiary.",
          " or ".join(f"memberOf({q('div-' + slug(s))})" for s in SUBSIDIARIES))

        # --- GitHub EMU access groups (groups of groups) ---
        g("engineering-org", "All engineering, including subsidiary engineering teams.",
          'memberOf("div-engineering") or memberOf("dept-ferrocene-engineering") '
          'or memberOf("dept-quillmark-engineering") or memberOf("dept-bluegate-research")')
        g("github-emu-users",
          "Master provisioning group: everyone who gets a GitHub EMU account.",
          '(memberOf("engineering-org") or memberOf("div-security") or memberOf("dept-corporate-it") '
          'or memberOf("team-technical-writing") or memberOf("team-developer-relations") '
          'or memberOf("team-solutions-engineering") or memberOf("team-quillmark-solutions") '
          'or memberOf("team-ferrocene-developer-community") or memberOf("team-enterprise-applications")) '
          'and memberOf("all-staff")')
        g("github-copilot", "Copilot seats: EMU users excluding contractors and interns.",
          'memberOf("github-emu-users") and not (memberOf("contractors") or memberOf("interns"))')
        g("github-enterprise-owners",
          "GitHub Enterprise Owners: CTO and CISO (static) plus the IAM team (dynamic).",
          'memberOf("team-identity-access-management") and memberOf("all-staff")',
          members=[p["email"] for p in self.people if p["title"] in ("Chief Technology Officer", "Chief Information Security Officer")])
        g("emea-engineering", "EMEA engineers (EU data-residency repositories).",
          'memberOf("engineering-org") and region == "EMEA" and memberOf("all-staff")')
        g("soc2-production-access", "Engineers with production access, in scope for SOC 2.",
          '(memberOf("dept-platform-engineering") or memberOf("team-detection-response") '
          'or memberOf("team-cloud-security")) and employmentType != "Contractor" and memberOf("all-staff")')
        g("incident-commanders", "Engineering and security managers on the incident-commander rota.",
          'memberOf("people-managers") and (memberOf("engineering-org") or memberOf("div-security"))')
        champions = []
        for team in ("Identity & Access", "Payments", "Core API", "Web Experience", "Mobile", "Integrations & Ecosystem"):
            senior = sorted((p for p in self.people if p["team"] == team and p["level"] in ("IC3", "IC4", "IC5")
                             and p["lifecycle"] == "employed"), key=lambda p: p["email"])
            if senior:
                champions.append(senior[0]["email"])
        g("security-champions", "Nominated product engineers (static) plus the AppSec team (dynamic).",
          'memberOf("team-application-security")', members=champions)
        cab = [p["email"] for p in self.people
               if p["title"] in ("Chief Information Officer", "Chief Information Security Officer",
                                 "Director, Corporate IT", "Chief Compliance Officer")]
        g("change-advisory-board", "Change Advisory Board: approves production changes (static membership).",
          members=cab)
        g("legal-and-compliance-reviewers", "Legal, privacy and GRC staff who review open-source and policy repos.",
          'memberOf("dept-legal") or memberOf("team-privacy-data-protection") '
          'or memberOf("team-governance-risk-compliance")')
        return out



DEFAULT_SEED = 20261114


def generate_dataset(seed=DEFAULT_SEED):
    """Build the organisation and return (users, groups) as lists of dicts."""
    gen = Generator(seed)
    gen.build()
    teams = [t[0] for _, _, _, depts in ORG.values() for d in depts for t in d[3]]
    assert len(teams) == len(set(teams)), "team names must be unique"
    reports = {}
    for p in gen.people:
        if p["manager"] and p["lifecycle"] != "terminated":
            reports.setdefault(p["manager"]["id"], []).append(p)
    users = [gen.to_hibob(p, reports) for p in gen.people]
    groups = gen.groups()
    names = [g["name"] for g in groups]
    assert len(names) == len(set(names)), "duplicate group names"
    return users, groups
