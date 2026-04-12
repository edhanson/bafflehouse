# Bafflehouse

Bafflehouse is a classic text-based interactive fiction game designed and developed to meet the capstone project requirements for the Quantic School of Business and Technology's M.S.S.E. program. It was designed to incorporate a vector embedding model into its natural language text parsing. It also has a learning algorithm in place so that the main enemy learns through combat to become a more adept foe across multiple sessions.

## Installation and Setup

Bafflehouse was designed and tested on Linux Mint Version 22.1 running a virtual environment with Python 3.12. Package requirements are detailed in the `requirements.txt` file. A virtual environment is recommended for local hosting. The embedding model runs locally and is deployed by entering:
`python download_local_model.py`

It can be verified by entering:
`python verify_local_model.py`

The game will run without the embedding model, but the parser will have more limited capabilities. To execute the game, enter:
`python main.py`

Note that you may need to replace `python` with `python3` in the above commands depending on the details of your system.

The game is self-contained with in-game help accessible via entering the `help` command.

A special online version that lacks the save game feature and the embedding model is located at:
https://bafflehouse.onrender.com/

More information about the project and its development is can be found in the design_and_testing.md file.