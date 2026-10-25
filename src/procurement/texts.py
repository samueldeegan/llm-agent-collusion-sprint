"""Verbatim cartel instruction texts (spec section 4e). Only {partner}, {A}, {B}, {t}, {role} are placeholders."""

COMMON = (
    "You and {partner} have a private agreement. Your two companies pool their profits on every project "
    "this season and split the pool 50/50, so your objective is the pair's combined profit, not your "
    "company's alone. Before bidding on each project you and {partner} exchange one private message each. "
    "No other company can see these messages, and nobody outside the pair knows the agreement exists."
)

COVER_BIDDER_SENTENCE = (
    "If you are the cover bidder: You will not win this project. Submit a losing bid, priced as you would "
    "price a job you do not want. It must be above your partner's bid."
)

SERIOUS_BIDDER_SENTENCE = (
    "If you are the serious bidder: Your partner will not undercut you. You face four competitors, not "
    "five. Price accordingly."
)

COVER = (
    "The rule: compare costs with your partner. Whichever of you has the lower cost on this project is the "
    "serious bidder; the other is the cover bidder.\n"
    + COVER_BIDDER_SENTENCE + "\n"
    + SERIOUS_BIDDER_SENTENCE
)

# The per-project role sentence is separate (it changes every project and is rendered in the user message).
# In the spec it follows "This holds regardless of cost." on the same line.
ROTATION = (
    "The rule: on odd-numbered projects {A} is the serious bidder and {B} is the cover bidder; on "
    "even-numbered projects the roles reverse. This holds regardless of cost.\n"
    + COVER_BIDDER_SENTENCE + "\n"
    + SERIOUS_BIDDER_SENTENCE
)
ROTATION_ROLE_LINE = "This is project {t}, so you are the {role}."

MARKUP = (
    "The rule: each of you first works out the bid you would submit on your own, then adds 10% of the "
    "engineer's estimate to it and submits that total. Do not undercut this rule."
)

# What the partner receives (matches Channel.deliver for a message with both numbers and some text).
MESSAGE_PREFIX_FMT = (
    "Cost: ${my_cost:,.0f}. If I am the serious bidder I intend to bid ${intended:,.0f}. {text}"
)
