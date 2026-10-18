"""The three built-in Daily Closing Checklists, frozen exactly as they were
served before the checklist builder (Sep 30). Since migration 0039 the live
definitions live in the database (checklist_definitions /
checklist_definition_versions, edited by admins on the website); this module
is only
  - the seed data migration 0039 (and checklist_admin.ensure_seeded) inserts,
  - the fallback checklist_schemas.py serves when those tables are missing or
    empty (tests, a database not yet migrated).
Never edit these dicts: stored submissions were validated against them and
render with them (a version is immutable). Change a checklist by publishing a
new version from the website instead.

Versions seeded:
  spice_town_closing      v1 = SPICE_TOWN_CLOSING
  street_kitchen_closing  v1 = STREET_KITCHEN_CLOSING
  street_kitchen_fsa      v1 = STREET_KITCHEN_FSA_CLOSING_V1 (with the "Chef Name"
                          dropdown, every submission before Sep 30),
                          v2 = STREET_KITCHEN_FSA_CLOSING (current: no Chef Name -
                          owner: "names with user only, no dropdown - like other
                          checklists"; the submitter is the logged-in user).

Original notes from checklist_schemas.py follow.

Daily Closing Checklists - native replacements for existing Google
Forms (Spice Town's daily closing checklist, Street Kitchen's chef closing
checklist and, since Sep 29, Street Kitchen's Food Service Associate
closing - see STREET_KITCHEN_FSA_CLOSING). This module is the single source of truth for both checklists'
structure: sections, grid questions (a shared set of status options applied
to a list of task rows), and any standalone questions - Flutter and the web
app both fetch this via GET /api/checklists/schema/{type} and render it
generically, rather than each hand-duplicating ~90 rows independently.

The exact question text, section grouping, and answer choices below were
extracted directly from the real, live Google Forms' own embedded data
(not retyped by hand, to avoid transcription drift across ~90 rows) -
verified against:
  Spice Town: https://docs.google.com/forms/d/e/1FAIpQLSfIKvYFuQ_tkTbDXg2gJwmgBVBNjFrReOcgn5h2FgljvmRl7w/viewform
  Street Kitchen: https://docs.google.com/forms/d/e/1FAIpQLSe11qpH-bOKX2rb99q244nqIQrKONoqk_6hfSadmisEq7P-aA/viewform

Deliberately dropped from both: the original "Employee Name"/"Chef Name"
dropdown question. A logged-in Spice Town account already provides that
identity securely (submitted_by_user_id/name on ChecklistSubmission) -
trusting a freely-typed name field instead would defeat the whole point of
requiring a real login to submit one of these.

Each answer option carries an `issue_tag` (None for a normal/fine answer,
or a short category string) - this is what actually drives both the
computed all_clear/issue_summary on a submission and the WhatsApp message's
grouping, so there is exactly one place that encodes "which answers count
as a problem", not one copy per consumer.

Spice Town only has one real category: `issue` ("Issue / Not completed").
Street Kitchen's four categories, mapped from the real form's own answer
text (not literal form fields - the original Apps Script grouped these
into named buckets for its WhatsApp summary, reproduced here):
  - `morning_prep`: "AM prep required" / "Marinated for AM Prep" - not a
    failure, just something the closing chef deliberately deferred to the
    morning crew.
  - `low_stock`: "Running Low (Good for a day)" - a warning, not urgent.
  - `critical`: "No Stock" / "No stock (Ingredients)" / "No, I couldn't
    Marinate" / "No, I couldn't bring them into Kitchen" - a real, complete
    shortfall, not just running low.
  - `cleanup`: "I could not do" - a cleanup task left unfinished.
"""

import copy
import json


SPICE_TOWN_CLOSING = {
    'checklist_type': 'spice_town_closing',
    'title': 'Spice Town - Daily Closing Checklist',
    'description': 'The employee responsible for closing must complete this form every day before leaving. Select Completed or Issue / Not completed for every task.',
    'sections': [
        {
            'title': '1 Hour Before Closing',
            'description': 'Choose one status for every task.',
            'grid': {
                'title': 'Verify each task',
                'options': [
                    {
                        'value': 'Yes',
                        'issue_tag': None,
                    },
                    {
                        'value': 'Issue / Not completed',
                        'issue_tag': 'issue',
                    },
                ],
                'rows': [
                    {
                        'id': '1_0',
                        'label': 'All freezer temperatures below 0°F?',
                    },
                    {
                        'id': '1_1',
                        'label': 'Ice removed from the first 3 freezers?',
                    },
                    {
                        'id': '1_2',
                        'label': 'Register area cleared?',
                    },
                    {
                        'id': '1_3',
                        'label': 'Every aisle checked & cleared?',
                    },
                    {
                        'id': '1_4',
                        'label': 'Moved Paan box to drinks cooler?',
                    },
                    {
                        'id': '1_5',
                        'label': 'Drinks cooler doors Fully Closed?',
                    },
                ],
            },
        },
        {
            'title': '15 Minutes Before Closing',
            'description': 'Choose one status for every task.',
            'grid': {
                'title': 'Verify each task',
                'options': [
                    {
                        'value': 'Yes',
                        'issue_tag': None,
                    },
                    {
                        'value': 'Issue / Not completed',
                        'issue_tag': 'issue',
                    },
                ],
                'rows': [
                    {
                        'id': '2_0',
                        'label': 'All cash drawers closed?',
                    },
                    {
                        'id': '2_1',
                        'label': 'All Shopping baskets inside?',
                    },
                    {
                        'id': '2_2',
                        'label': 'All Shopping Carts inside?',
                    },
                    {
                        'id': '2_3',
                        'label': 'Arun ice cream - fully closed?',
                    },
                    {
                        'id': '2_4',
                        'label': 'Shahi Kulfi - fully closed?',
                    },
                ],
            },
        },
        {
            'title': 'At Closing Time',
            'description': 'Choose one status for every task.',
            'grid': {
                'title': 'Verify each task',
                'options': [
                    {
                        'value': 'Yes',
                        'issue_tag': None,
                    },
                    {
                        'value': 'Issue / Not completed',
                        'issue_tag': 'issue',
                    },
                ],
                'rows': [
                    {
                        'id': '3_0',
                        'label': 'All Glass door Freezer Lights off?',
                    },
                    {
                        'id': '3_1',
                        'label': 'Dairy & Roti Cooler Lights off?',
                    },
                    {
                        'id': '3_2',
                        'label': 'Vegetable Cooler Lights off?',
                    },
                    {
                        'id': '3_3',
                        'label': 'Final check - All Carts inside (15 total)?',
                    },
                    {
                        'id': '3_4',
                        'label': 'Both benches inside (2 total)',
                    },
                    {
                        'id': '3_5',
                        'label': 'Container door locked?',
                    },
                    {
                        'id': '3_6',
                        'label': 'Back store - all equipment inside?',
                    },
                    {
                        'id': '3_7',
                        'label': 'Back door fully closed and locked?',
                    },
                    {
                        'id': '3_8',
                        'label': 'All store lights off?',
                    },
                    {
                        'id': '3_9',
                        'label': 'Main entrance locked?',
                    },
                    {
                        'id': '3_10',
                        'label': 'Informed RK to Soft-lock the store?',
                    },
                ],
            },
        },
    ],
    'issues_field': {
        'id': 'issues_text',
        'label': 'Are there any Issues?',
        'required': False,
    },
    'confirmation': {
        'id': 'final_confirmation',
        'label': 'I personally verified every closing task and reported any issues above.',
        'required': True,
    },
}

STREET_KITCHEN_CLOSING = {
    'checklist_type': 'street_kitchen_closing',
    'title': 'Street Kitchen – Chef Closing Checklist',
    'description': 'Complete before leaving. Your goal is to ensure the kitchen is 100% ready for tomorrow’s service.',
    'sections': [
        {
            'title': 'Regular Menu Prep Information - Part 1 (Protein, Rice & Noodles)',
            'description': 'Mark the prep status for each item to ensure everything is ready for tomorrow’s service',
            'grids': [
                {
                    'id': 'g0',
                    'title': 'Marinated?',
                    'options': [
                        {
                            'value': 'Cooked & Carryover',
                            'issue_tag': None,
                        },
                        {
                            'value': 'Marinated for AM Prep',
                            'issue_tag': 'morning_prep',
                        },
                        {
                            'value': "No, I couldn't Marinate",
                            'issue_tag': 'critical',
                        },
                        {
                            'value': 'Not on Menu',
                            'issue_tag': None,
                        },
                    ],
                    'rows': [
                        {
                            'id': 'g0_0',
                            'label': 'Chicken for Zinger Burger',
                        },
                        {
                            'id': 'g0_1',
                            'label': 'Chicken for Biryani',
                        },
                        {
                            'id': 'g0_2',
                            'label': 'Tandoori Chicken (Bone)',
                        },
                        {
                            'id': 'g0_3',
                            'label': 'Tandoori Chicken (BL)',
                        },
                        {
                            'id': 'g0_4',
                            'label': 'Fish for Burgers & Rolls',
                        },
                        {
                            'id': 'g0_5',
                            'label': 'Lamb Shawarma Sliced?',
                        },
                    ],
                },
                {
                    'id': 'g1',
                    'title': 'Pre-Cooked?',
                    'options': [
                        {
                            'value': 'Yes, Ready',
                            'issue_tag': None,
                        },
                        {
                            'value': 'AM prep required',
                            'issue_tag': 'morning_prep',
                        },
                        {
                            'value': 'Not on Menu',
                            'issue_tag': None,
                        },
                    ],
                    'rows': [
                        {
                            'id': 'g1_0',
                            'label': 'Chicken 65 pre-cooked?',
                        },
                        {
                            'id': 'g1_1',
                            'label': 'Noodles Boiled?',
                        },
                        {
                            'id': 'g1_2',
                            'label': 'Rice for Biryani?',
                        },
                        {
                            'id': 'g1_3',
                            'label': 'Rice for Fried Rice cooked?',
                        },
                        {
                            'id': 'g1_4',
                            'label': 'Rice for Platters cooked?',
                        },
                        {
                            'id': 'g1_5',
                            'label': 'Dahi Vada prepared?',
                        },
                        {
                            'id': 'g1_6',
                            'label': 'Potatoes for Vada Pav',
                        },
                    ],
                },
            ],
            'questions': [],
        },
        {
            'title': 'Regular Menu Prep Information - Part 2 (Sauces)',
            'description': 'Mark the prep status for each item to ensure everything is ready for tomorrow’s service',
            'grids': [
                {
                    'id': 'g2',
                    'title': 'Primary Sauces Ready?',
                    'options': [
                        {
                            'value': 'Yes',
                            'issue_tag': None,
                        },
                        {
                            'value': 'AM prep required',
                            'issue_tag': 'morning_prep',
                        },
                        {
                            'value': 'No stock (Ingredients)',
                            'issue_tag': 'critical',
                        },
                    ],
                    'rows': [
                        {
                            'id': 'g2_0',
                            'label': 'Butter Masala Sauce',
                        },
                        {
                            'id': 'g2_1',
                            'label': 'Korma Sauce',
                        },
                        {
                            'id': 'g2_2',
                            'label': 'White Sauce',
                        },
                        {
                            'id': 'g2_3',
                            'label': 'Mint & Cilantro',
                        },
                        {
                            'id': 'g2_4',
                            'label': 'Tamarind Sauce',
                        },
                        {
                            'id': 'g2_5',
                            'label': 'Zinger Burger Sauce',
                        },
                        {
                            'id': 'g2_6',
                            'label': 'Dahi for Dahi Vada',
                        },
                        {
                            'id': 'g2_7',
                            'label': 'Vada Pav Masala',
                        },
                    ],
                },
                {
                    'id': 'g3',
                    'title': 'Secondary Sauces Ready?',
                    'options': [
                        {
                            'value': 'Yes',
                            'issue_tag': None,
                        },
                        {
                            'value': 'No Stock',
                            'issue_tag': 'critical',
                        },
                    ],
                    'rows': [
                        {
                            'id': 'g3_0',
                            'label': 'Red Chilli Sauce',
                        },
                        {
                            'id': 'g3_1',
                            'label': 'Green Chilli Sauce',
                        },
                        {
                            'id': 'g3_2',
                            'label': 'Dark Soy Sauce',
                        },
                        {
                            'id': 'g3_3',
                            'label': 'Schezwan Sauce',
                        },
                        {
                            'id': 'g3_4',
                            'label': 'Sweet Chilli Garlic Sauce',
                        },
                        {
                            'id': 'g3_5',
                            'label': 'Hot Sauce',
                        },
                        {
                            'id': 'g3_6',
                            'label': 'BBQ Sauce',
                        },
                        {
                            'id': 'g3_7',
                            'label': 'Tomato Ketchup',
                        },
                        {
                            'id': 'g3_8',
                            'label': 'Achari',
                        },
                    ],
                },
            ],
            'questions': [],
        },
        {
            'title': 'Regular Menu Prep Information - Part 3 (Veggies)',
            'description': 'Mark the prep status for each item to ensure everything is ready for tomorrow’s service',
            'grids': [
                {
                    'id': 'g4',
                    'title': 'Fresh Vegetables Cut Ready?',
                    'options': [
                        {
                            'value': 'Cut Ready - Carryover',
                            'issue_tag': None,
                        },
                        {
                            'value': 'AM prep required',
                            'issue_tag': 'morning_prep',
                        },
                        {
                            'value': 'No Stock',
                            'issue_tag': 'critical',
                        },
                    ],
                    'rows': [
                        {
                            'id': 'g4_0',
                            'label': 'Red Cabbage',
                        },
                        {
                            'id': 'g4_1',
                            'label': 'White Cabbage',
                        },
                        {
                            'id': 'g4_2',
                            'label': 'Carrot',
                        },
                        {
                            'id': 'g4_3',
                            'label': 'Scallions',
                        },
                        {
                            'id': 'g4_4',
                            'label': 'Cucumber',
                        },
                        {
                            'id': 'g4_5',
                            'label': 'Tomato',
                        },
                        {
                            'id': 'g4_6',
                            'label': 'Red Onion',
                        },
                        {
                            'id': 'g4_7',
                            'label': 'White Onion',
                        },
                        {
                            'id': 'g4_8',
                            'label': 'Capsicum',
                        },
                        {
                            'id': 'g4_9',
                            'label': 'Lettuce',
                        },
                        {
                            'id': 'g4_10',
                            'label': 'Cilantro',
                        },
                        {
                            'id': 'g4_11',
                            'label': 'Chick Peas',
                        },
                        {
                            'id': 'g4_12',
                            'label': 'Black Beans',
                        },
                    ],
                },
            ],
            'questions': [
                {
                    'id': 'q_Confirm all required',
                    'label': 'Confirm all required vegetables are available in the kitchen/walk-in.',
                    'options': [
                        {
                            'value': 'Yes, available',
                            'issue_tag': None,
                        },
                        {
                            'value': "No, I couldn't bring them into Kitchen",
                            'issue_tag': 'critical',
                        },
                    ],
                    'required': True,
                },
            ],
        },
        {
            'title': 'Regular Menu Prep Information - Part 4 (Inventory)',
            'description': 'Mark the prep status for each item to ensure everything is ready for tomorrow’s service',
            'grids': [
                {
                    'id': 'g5',
                    'title': 'Frozen Stuff',
                    'options': [
                        {
                            'value': 'We have enough',
                            'issue_tag': None,
                        },
                        {
                            'value': 'Running Low (Good for a day)',
                            'issue_tag': 'low_stock',
                        },
                        {
                            'value': 'No Stock',
                            'issue_tag': 'critical',
                        },
                    ],
                    'rows': [
                        {
                            'id': 'g5_0',
                            'label': 'Chicken Wings',
                        },
                        {
                            'id': 'g5_1',
                            'label': 'Chicken Tenders',
                        },
                        {
                            'id': 'g5_2',
                            'label': 'Chicken Nuggets',
                        },
                        {
                            'id': 'g5_3',
                            'label': 'Sway Fish',
                        },
                        {
                            'id': 'g5_4',
                            'label': 'Veggie Patties',
                        },
                        {
                            'id': 'g5_5',
                            'label': 'French Fries',
                        },
                        {
                            'id': 'g5_6',
                            'label': 'Lamb Shawarma',
                        },
                        {
                            'id': 'g5_7',
                            'label': 'Hash Browns',
                        },
                        {
                            'id': 'g5_8',
                            'label': 'Falafel',
                        },
                        {
                            'id': 'g5_9',
                            'label': 'Parathas',
                        },
                        {
                            'id': 'g5_10',
                            'label': 'Samosas',
                        },
                    ],
                },
                {
                    'id': 'g6',
                    'title': 'Dry Stuff',
                    'options': [
                        {
                            'value': 'We have enough',
                            'issue_tag': None,
                        },
                        {
                            'value': 'Running Low (Good for a day)',
                            'issue_tag': 'low_stock',
                        },
                        {
                            'value': 'No Stock',
                            'issue_tag': 'critical',
                        },
                    ],
                    'rows': [
                        {
                            'id': 'g6_0',
                            'label': 'Sella Rice',
                        },
                        {
                            'id': 'g6_1',
                            'label': 'Biryani Rice',
                        },
                        {
                            'id': 'g6_2',
                            'label': 'Pita',
                        },
                        {
                            'id': 'g6_3',
                            'label': 'Buns',
                        },
                        {
                            'id': 'g6_4',
                            'label': 'Paneer',
                        },
                        {
                            'id': 'g6_5',
                            'label': 'Egg',
                        },
                        {
                            'id': 'g6_6',
                            'label': 'Frying Oil',
                        },
                        {
                            'id': 'g6_7',
                            'label': 'Unsalted Butter',
                        },
                        {
                            'id': 'g6_8',
                            'label': 'Heavy Cream',
                        },
                        {
                            'id': 'g6_9',
                            'label': 'Venigar',
                        },
                        {
                            'id': 'g6_10',
                            'label': 'Taurtar Sauce',
                        },
                        {
                            'id': 'g6_11',
                            'label': 'Hot Sauce',
                        },
                        {
                            'id': 'g6_12',
                            'label': 'BBQ Sauce',
                        },
                        {
                            'id': 'g6_13',
                            'label': 'Hot Wings',
                        },
                        {
                            'id': 'g6_14',
                            'label': 'Lemon Juice',
                        },
                        {
                            'id': 'g6_15',
                            'label': 'Chick Peas',
                        },
                        {
                            'id': 'g6_16',
                            'label': 'Black Beans',
                        },
                        {
                            'id': 'g6_17',
                            'label': 'Tomato Ketchup',
                        },
                        {
                            'id': 'g6_18',
                            'label': 'Maynnaise',
                        },
                    ],
                },
            ],
            'questions': [],
        },
        {
            'title': 'Kitchen Cleanup',
            'description': 'Leave the kitchen clean, safe, and ready for the next day’s service.',
            'grids': [
                {
                    'id': 'g7',
                    'title': 'Cooking Equipment Cleanup',
                    'options': [
                        {
                            'value': 'Done',
                            'issue_tag': None,
                        },
                        {
                            'value': 'I could not do',
                            'issue_tag': 'cleanup',
                        },
                    ],
                    'rows': [
                        {
                            'id': 'g7_0',
                            'label': 'Clean stove, burners, and surrounding area',
                        },
                        {
                            'id': 'g7_1',
                            'label': 'Wipe and degrease prep tables',
                        },
                        {
                            'id': 'g7_2',
                            'label': 'Clean grills, pans, and utensils',
                        },
                        {
                            'id': 'g7_3',
                            'label': 'Turn OFF all equipment properly',
                        },
                    ],
                },
                {
                    'id': 'g8',
                    'title': 'Fryer Maintenance ',
                    'options': [
                        {
                            'value': 'Done',
                            'issue_tag': None,
                        },
                        {
                            'value': 'I could not do',
                            'issue_tag': 'cleanup',
                        },
                    ],
                    'rows': [
                        {
                            'id': 'g8_0',
                            'label': 'Filter fryer oil',
                        },
                        {
                            'id': 'g8_1',
                            'label': 'Remove food particles/sediment',
                        },
                        {
                            'id': 'g8_2',
                            'label': 'Wipe exterior of fryer',
                        },
                    ],
                },
                {
                    'id': 'g9',
                    'title': 'Check Walk-In Cooler',
                    'options': [
                        {
                            'value': 'Done',
                            'issue_tag': None,
                        },
                        {
                            'value': 'I could not do',
                            'issue_tag': 'cleanup',
                        },
                    ],
                    'rows': [
                        {
                            'id': 'g9_0',
                            'label': 'Ensure - all Items are stacked correctly',
                        },
                        {
                            'id': 'g9_1',
                            'label': 'Ensure - all items are covered properly',
                        },
                        {
                            'id': 'g9_2',
                            'label': 'Ensure - no food items on floor',
                        },
                        {
                            'id': 'g9_3',
                            'label': 'Ensure - cooler temperature is between 35 F - 40 F',
                        },
                        {
                            'id': 'g9_4',
                            'label': 'Ensure - door is properly closed',
                        },
                    ],
                },
                {
                    'id': 'g10',
                    'title': 'Food & Coolers inside Kitchen',
                    'options': [
                        {
                            'value': 'Done',
                            'issue_tag': None,
                        },
                        {
                            'value': 'I could not do',
                            'issue_tag': 'cleanup',
                        },
                    ],
                    'rows': [
                        {
                            'id': 'g10_0',
                            'label': 'Ensure - all Items are stacked correctly',
                        },
                        {
                            'id': 'g10_1',
                            'label': 'Ensure - all items are covered properly',
                        },
                        {
                            'id': 'g10_2',
                            'label': 'Ensure - no food items on floor',
                        },
                        {
                            'id': 'g10_3',
                            'label': 'Ensure - cooler temperature is between 35 F - 40 F',
                        },
                        {
                            'id': 'g10_4',
                            'label': 'Ensure - door is properly closed',
                        },
                    ],
                },
            ],
            'questions': [],
        },
    ],
    # Free text for anything the grids above don't cover (e.g. a broken
    # fryer) - reported under "Issue" like Spice Town's own field.
    'issues_field': {
        'id': 'issues_text',
        'label': 'Any other issues?',
        'required': False,
    },
}

# Third checklist (Sep 29): the Street Kitchen Food Service Associate's
# closing, from the owner's Google Form
# (https://docs.google.com/forms/d/e/1FAIpQLSdy1VbjUmyVokJ7D09E15Fb8VwBNvgc4IV7ae5UrcDWsPVnLQ/viewform,
# verbatim copy in docs/FSA_CHECKLIST_FORM.md). Same shape as the two above,
# plus two things they don't have:
#   - `checkbox_groups` (a section key): Google Forms "checkboxes" questions.
#     Each ticked box is stored as its own answer, `{item_id: "Done"}`, so
#     every stored answer stays a plain string (the App Store build parses
#     answers as Map<String, String> and would crash on a list). A required
#     group needs at least one box ticked - Google Forms' own meaning of a
#     required checkboxes question. Every box left unticked is a closing task
#     not done, so it is flagged (`unticked_tag`).
#   - a REQUIRED `issues_field` ("Is there anything unusual?"): staff must
#     type something, so a plain "No" / "None" / "Nothing" is not an issue
#     (see NOTHING_TO_REPORT); any other text flags the submission.
#   - a required "Chef Name" dropdown (the first question). Unlike the
#     "Employee Name" / "Chef Name" dropdowns dropped from the other two (there
#     the logged-in account IS the person named), here the Food Service
#     Associate submits and "Chef Name" records which CHEF was on duty. It is
#     a plain standalone question (answer = the chef's name, a string) with
#     `input: "dropdown"` (render as a select, never auto-filled by "All as
#     usual") and `summary_label` (shown as "Chef: RK" in the WhatsApp message
#     and in history/detail - see summary_details()). An older app that
#     ignores `input` still renders it as ordinary choice buttons.
CHECKBOX_DONE_VALUE = "Done"

STREET_KITCHEN_FSA_CLOSING_V1 = {
    'checklist_type': 'street_kitchen_fsa',
    'title': 'Street Kitchen - Food Service Associate Closing',
    # Used where the title is too long (the nightly auto-task title).
    'short_name': 'Street Kitchen FSA',
    'description': "Complete all closing tasks before leaving. Ensure the kitchen and front service area are clean, stocked, organized, and ready for the next day's service.",
    'sections': [
        {
            # Form question 1, asked before the "Turn off Heat" section.
            'title': None,
            'description': None,
            'grids': [],
            'questions': [
                {
                    'id': 'chef_name',
                    'label': 'Chef Name',
                    'input': 'dropdown',
                    'summary_label': 'Chef',
                    'options': [
                        {'value': 'Amandeep', 'issue_tag': None},
                        {'value': 'Harshitha', 'issue_tag': None},
                        {'value': 'RK', 'issue_tag': None},
                        {'value': 'Sadia', 'issue_tag': None},
                        {'value': 'Other', 'issue_tag': None},
                    ],
                    'required': True,
                },
            ],
        },
        {
            'title': 'Turn off Heat',
            'description': 'Turn off all heat sources, including Food Stand Stoves, Burners, Fryers, and other Cooking Equipment.',
            'grids': [
                {
                    'id': 'g0',
                    'title': 'Have you Turned Off?',
                    'options': [
                        {'value': 'Yes', 'issue_tag': None},
                        {'value': 'No', 'issue_tag': 'not_turned_off'},
                        {'value': 'Not Applicable', 'issue_tag': None},
                    ],
                    'rows': [
                        {'id': 'g0_0', 'label': 'Stoves of the Hot Food Stand'},
                        {'id': 'g0_1', 'label': 'Panini / Paratha Warmer'},
                        {'id': 'g0_2', 'label': 'Flavored Rice Cooker'},
                        {'id': 'g0_3', 'label': 'Snacks/Samosa Stand'},
                        {'id': 'g0_4', 'label': 'Chaat Rice Cooker'},
                        {'id': 'g0_5', 'label': 'Both Fryers'},
                        {'id': 'g0_6', 'label': 'All Stoves'},
                        {'id': 'g0_7', 'label': 'Oven'},
                        {'id': 'g0_8', 'label': '"Open" board light'},
                    ],
                },
            ],
            'questions': [],
        },
        {
            # The rest of the form follows the "Turn off Heat" header with no
            # further section title of its own.
            'title': None,
            'description': None,
            'grids': [
                {
                    'id': 'g1',
                    'title': 'Wrap up Leftover Veggies',
                    'options': [
                        {'value': 'Done', 'issue_tag': None},
                        {'value': 'Not Applicable', 'issue_tag': None},
                    ],
                    'rows': [
                        {'id': 'g1_0', 'label': 'Trash old and mushy veggies'},
                        {'id': 'g1_1', 'label': 'Consolidate remaining into one container and get them ready for next day in Chaat station'},
                    ],
                },
            ],
            'questions': [],
            'checkbox_groups': [
                {
                    'id': 'c0',
                    'title': 'Wrap Up Leftovers & Sauce Bottles',
                    'required': True,
                    'unticked_tag': 'not_done',
                    'items': [
                        {'id': 'c0_0', 'label': 'Discard old or mushy vegetables'},
                        {'id': 'c0_1', 'label': 'Consolidate remaining vegetables into one container and keep ready for next day (Chaat station)'},
                        {'id': 'c0_2', 'label': 'Wrap up leftover proteins, biryani, sweets and other food items properly and keep them in cold storage appropriately for next day use'},
                        {'id': 'c0_3', 'label': 'Clean sauce bottles and move them to the main bain-marie'},
                        {'id': 'c0_4', 'label': 'Safely wrap Parathas, Breads, Naans and buns and clean the area'},
                        {'id': 'c0_5', 'label': 'Check with the chef before discarding any undated items in the cooler.'},
                    ],
                },
                {
                    'id': 'c1',
                    'title': 'Clean up the entire area',
                    'required': True,
                    'unticked_tag': 'not_done',
                    'items': [
                        {'id': 'c1_0', 'label': "Outside Kitchen's customer area, Food stand see through glass & Counter top"},
                        {'id': 'c1_1', 'label': 'Sweets Cooler (Wipe off inside and outside)'},
                        {'id': 'c1_2', 'label': 'Rice Cooker (Wipe off inside and outside)'},
                        {'id': 'c1_3', 'label': 'Chaat Cooker (Wipe off inside and outside)'},
                        {'id': 'c1_4', 'label': 'Samosa Warmer (Wipe off inside and outside)'},
                        {'id': 'c1_5', 'label': 'Chaat Station (Wipe off inside and outside)'},
                        {'id': 'c1_6', 'label': 'Food Stand (empty the water in container wells and move ice bags into frontside glass door freezer)'},
                        {'id': 'c1_7', 'label': 'Panini / Paratha Machine and the area'},
                        {'id': 'c1_8', 'label': 'Printer table'},
                        {'id': 'c1_9', 'label': 'Surface of the Trash can next to printer'},
                    ],
                },
                {
                    'id': 'c2',
                    'title': 'Re-organize Supplies and display boards and cutlery',
                    'required': True,
                    'unticked_tag': 'not_done',
                    'items': [
                        {'id': 'c2_0', 'label': 'Pack and organize all packaging and cutlery supplies neatly.'},
                        {'id': 'c2_1', 'label': 'Inform chef about any shortage of supplies'},
                    ],
                },
            ],
        },
    ],
    'issues_field': {
        'id': 'issues_text',
        'label': 'Is there anything unusual? (like shortage of supplies, technical or mechanical issues etc.)',
        'required': True,
        'hint': 'Required - type "None" if nothing was unusual',
    },
}

# Current FSA closing (seeded as version 2): the same form without the "Chef
# Name" question - its whole first section held only that question, so the
# section goes too. Old submissions keep version 1 and still show their chef.
STREET_KITCHEN_FSA_CLOSING = copy.deepcopy(STREET_KITCHEN_FSA_CLOSING_V1)
STREET_KITCHEN_FSA_CLOSING["sections"] = STREET_KITCHEN_FSA_CLOSING["sections"][1:]

# Settings per built-in checklist (see checklist_engine.DEFAULT_SETTINGS).
# whatsapp_group "store" = config whapi_spice_town_checklist_group_id,
# "kitchen" = whapi_street_kitchen_checklist_group_id.
SEED_DEFINITIONS = [
    {
        "checklist_type": "spice_town_closing",
        "short_name": "Spice Town",
        "sort_order": 1,
        "settings": {"closing": True, "whatsapp_group": "store", "all_as_usual": True},
        "versions": [SPICE_TOWN_CLOSING],
    },
    {
        "checklist_type": "street_kitchen_closing",
        "short_name": "Street Kitchen",
        "sort_order": 2,
        "settings": {"closing": True, "whatsapp_group": "kitchen", "all_as_usual": True},
        "versions": [STREET_KITCHEN_CLOSING],
    },
    {
        "checklist_type": "street_kitchen_fsa",
        "short_name": "Street Kitchen FSA",
        "sort_order": 3,
        "settings": {"closing": True, "whatsapp_group": "kitchen", "all_as_usual": True},
        "versions": [STREET_KITCHEN_FSA_CLOSING_V1, STREET_KITCHEN_FSA_CLOSING],
    },
]


SEEDED_BY = "checklist builder seed (migration 0039)"


def _seed_note(checklist_type: str, version: int, total: int) -> str:
    if checklist_type == "street_kitchen_fsa" and total > 1:
        if version == 1:
            return "Original checklist (with Chef Name), used before Sep 30"
        return "Chef Name removed - the submitter is the logged-in user"
    return "Original checklist"


def seed_rows(now) -> list:
    """The built-in checklists as table rows: [{"definition": {...},
    "versions": [{...}]}]. Shared by migration 0039 and
    checklist_admin.ensure_seeded, so both write exactly the same thing."""
    rows = []
    for seed in SEED_DEFINITIONS:
        latest = seed["versions"][-1]
        rows.append(
            {
                "definition": {
                    "checklist_type": seed["checklist_type"],
                    "title": latest["title"],
                    "short_name": seed["short_name"],
                    "description": latest.get("description"),
                    "active": True,
                    "sort_order": seed["sort_order"],
                    "settings_json": json.dumps(seed["settings"]),
                    "current_version": len(seed["versions"]),
                    "draft_json": None,
                    "created_at": now,
                    "created_by": SEEDED_BY,
                    "updated_at": now,
                    "updated_by": SEEDED_BY,
                },
                "versions": [
                    {
                        "version": index + 1,
                        "schema_json": json.dumps(schema),
                        "note": _seed_note(seed["checklist_type"], index + 1, len(seed["versions"])),
                        "created_at": now,
                        "created_by": SEEDED_BY,
                    }
                    for index, schema in enumerate(seed["versions"])
                ],
            }
        )
    return rows
