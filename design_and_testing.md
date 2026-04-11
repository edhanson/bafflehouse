# Bafflehouse

Bafflehouse is a classic text-based interactive fiction game designed and developed to meet the capstone project requirements for the Quantic School of Business and Technology's M.S.S.E. program.

## Design Overview

There are two particular innovative concepts that inspired this project. The general idea was to build a text-based interactive fiction adventure game in the general spirit of Zork and Colossal Cave Adventure. The general idea is to guide a player character through an imaginary world described by verbal text. The player navigates the world by typing commands that indicate the player character's intended physical interaction with the world, the objects within, and any denizens of the world. These text commands are interpreted by a natural language parser and the game responses with detailed descriptions of the consequences of your player character's actions.

The first and most obvious innovation was an attempt to harness the natural language processing capabilities present in modern AI chatbots to interpret the user's commands to guide the player character's actions. A typical frustration point with this sort of game is the limited vocabulary of the language parser and the precise statements one must enter to make progress in the game. In the end, the parser works via a combination of this approach and more traditional text processing paradigms.

The other innovation was to incorporate a machine learning aspect into the behavior of the non-player character denizens of the game's world. In the current manifestation, there are three such denizens, a cat whose behavior ranges from frightened/indifferent to loyal devotion, a troll whose behavior is handled through traditional game mechanics, and a hostile slime golem who learns from previous battles. The golem has a persistent "race memory" as it's behavior is partially governed by a machine learning component that maintains state across repeated gameplay sessions. The idea is that it essentially learns from past mistakes much as a human player might.

## Parser Details

One of the design requirements that informed the parser's conceptual design was the to not require online access to play the game. That ruled out the direct harnessing of hosted LLM machinery as the code was specifically designed to avoid the need for providing an API key, subscribing to paid LLM services, or having the game functionality mitigated by the imposition of external limits. As such, the code uses a locally cached copy of the `all-MiniLM-L6-v2` model to handle vector embeddings, but is also designed to function in the absence of the embedding model.

Roughly speaking, the parser has several layers. It cleans the inputs by normalizing the input to be more homogeneous, stripping preamble expressions that are used in natural language in ways that are not meaningful to the game engine, and rewriting idiomatic expressions. It then runs inputs through a three-layer verb identification pipeline, that attempts a literal match, typo correction, and then a synonym lookup (in that order) before resorting to semantic embedding routing via the vector embedding model when all else fails. It splits sentences based on verb definitions and how common structural paradigms correspond to verb choice. It makes attempts to resolve pronouns and can split compound commands. It also employs scoring mechanisms to rank competing interpretations of the input text. More specifically, noun phrases are matched to entity IDs relating to the game world through a multi-step process.

More technical details and relevant code are contained in the `parser.py` and `ir.py` files.

## NPC Behavior Details

As mentioned above, the troll's behavior is controlled through standard game mechanics, whereas the cat and the golem have more advanced algorithms governing their behavior. In terms of the cat, it is governed by a Bayesian reputation model with a simple rule-based behavior layer. This allows it to learn trust (or lack thereof) based on how the player interacts with the cat. Positive behaviors that the cat appreciates (such as feeding and petting) increase its loyalty, whereas negative behaviors such as attacking the cat diminish its loyalty. The cat's disposition resets between game plays. More technical details and relevant code are contained in the `npc.py` and `npc_bayesian.py` files.

A more advanced tabular Q-learning algorithm was deployed to govern the golem's behavior. Unlike the cat, this does not reset between game plays, with the goal being for the golem to change and improve its responses to the tactics and methods the player uses against it in combat. In general terms, there are 7 general actions the player may take and 6 that the golem might take. A set of 9 events were encoded with reward/punishment values to influence the golem's likely action given various combat states. More technical details and relevant code are contained in the `npc_qlearning.py` file.

## Coding and Development

To facilitate and accelerate the coding of this project, extensive use was made of AI chatbots. The initial stages of the project were coded with the assistance of ChatGPT. Here is a link to the relevant conversation:

https://chatgpt.com/share/698ab819-1494-800b-b0eb-29cfed8fccf8

Partway through the project, the decision was made to switch the conversation over to Claude. There are multiple relevant conversations that took place relating to the development:

https://claude.ai/share/84097462-ad75-43ef-a7cf-9fbff5050c35
https://claude.ai/share/2390c18c-34f8-43f1-abc9-15fea22bd969
https://claude.ai/share/1e001e4d-92c4-45df-85c7-d042f47aaeab

A Trello board relating to the design process is located here:

https://trello.com/invite/b/69a662e4f72d8d716a5ce25a/ATTI5211371a244b154db52768cec047559fB721D2E1/quantic-capstone-project

During development, extensive use was also made of issue tracking within the GitHub repository.

## General Architectural Design Decisions

The coding of the game in Python and its presentation across multiple files was performed with object-oriented design principles in mind. A modest attempt was made to honor the SOLID principles of sound object-oriented design. The last conversation with the Claude Chatbot mentioned in the previous section details a late discussion between the developer and Claude where Claude gave a generally positive assessment of the object-oriented design. However, much concern was raised over the bloating of the `engine.py` script at the time, as the development leaned too heavily on this script as an accumulation point of various bits of disparate functionality. Though not all phases of Claude's suggestions were implemented, the first and most significant phase was enacted, which significantly diminished the number of functions packed into `engine.py` by introducing the `handlers` module that currently contains 10 files to help with handling various game actions.

The resulting architecture pays general respect to the SOLID principles, particularly given its broad scope and the number of different components of the world that is modeling. Though no doubt deeper refinements could be orchestrated, most of the various game components pay heed to the SOLID principles. The parser in particular follows sound object-oriented design principles, including a factory method as demonstrated by `build_default()` within the `ParserSystem` class.

## Testing

During the latter portion of the development facilitated by Claude, it was noted that Claude apparently maintained a test suite behind the scenes that it was already using to test the code against various examples. This resulted in a portion of the first chat leading to the development of the `test_suite.py` app which was incorporated into a Github action to enact CI/CD testing. The test suite runs two layers of tests, symbolic and semantic, with the symbolic test running with the basic symbolic parsing capabilities in place and the semantic making full use of the semantic text parser. These were incorporated into Github actions to enact a weekly test that runs on Mondays and a test on each push to the main repository.

Unfortunately, the nature of interactive fiction means that numerous bugs can lie undetected within the parser as it may mishandle certain inputs, resulting in strange messages and/or unexpected responses in game behavior. This meant that deep testing by humans needed to be conducted, which was a particular pain point for the project. Most of that testing was conducted as a solo effort by the sole project author/contributor who also occasionally employed third-party assistance from others who tried playing the game mid-development. As such, a logging feature was introduced to facilitate reviewing the text output of each game session.
