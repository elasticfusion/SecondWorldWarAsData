# config.py

API_KEY = os.getenv('XAI_API_KEY')
MODEL = "grok-4-1-fast-reasoning"  # Adjust to the appropriate model name if needed, e.g., 'grok-4'

# Variables from the bash script
BOOK = "BreakoutAndPursuit"
BOOKNAME = "Breakout and Pursuit"
TYPE = "place"

# # Variables from the bash script
# BOOK = "BreakoutAndPursuit"
# BOOKNAME = "Breakout and Pursuit"
# #CHAPTER = 4
# #SECTION = "a"
# TYPE = "place"
# SOURCELINK = f"https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/USA-E-Breakout-{CHAPTER}.html"
# FOOTNOTELINK = f"https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/fn{CHAPTER}.html"
# LOCALSOURCE = f"chapter{CHAPTER}{SECTION}-event.json"
# CHAPTERFOLDER = f"../{BOOK}/data/prompts/chapter{CHAPTER}"
# REVIEWFOLDER = f"chapter{CHAPTER}{SECTION}-review"
# TARGETFILE = f"00-chapter{CHAPTER}{SECTION}-{TYPE}-review.yaml"
# OUTPUT_FILE = f"chapter{CHAPTER}{SECTION}-{TYPE}.json"