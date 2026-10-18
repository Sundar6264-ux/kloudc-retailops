"""Made-up vocabulary for the DEMO store (generate_demo_data.py).

Rules (plan 3.1a, MT13): nothing here is a real person, a real brand or a
real business. Products are generic descriptions ("Basmati Rice 10 lb"),
vendors are visibly fictitious ("Demo Produce Supply"), staff are roles
("grocery_lead"), contact addresses use the reserved ``.example`` domain
and phone numbers the 555-01xx fiction range.
"""

STORE_NAME = "StoreOps Demo Market"
STORE_CODE = "DEMO"

# vendor -> (delivery weekdays Mon=0..Sun=6, lead days, contact email)
VENDORS = {
    "Demo Produce Supply": ([0, 2, 4], 1, "orders@produce.demo-vendor.example"),
    "Sample Dairy Distributors": ([1, 4], 1, "orders@dairy.demo-vendor.example"),
    "Example Dry Goods Wholesale": ([2], 2, "orders@drygoods.demo-vendor.example"),
    "Demo Beverage Distributors": ([3], 2, "orders@beverage.demo-vendor.example"),
    "Placeholder Frozen Foods": ([1], 2, "orders@frozen.demo-vendor.example"),
    "Demo Bakery Supply": ([0, 3, 5], 1, "orders@bakery.demo-vendor.example"),
    "Demo Household Supply": ([4], 3, "orders@household.demo-vendor.example"),
    "Demo Snack Wholesale": ([2], 2, "orders@snacks.demo-vendor.example"),
}

# A second vendor some items can also come from (semicolon list in the CSV).
SECONDARY_VENDOR = {
    "Example Dry Goods Wholesale": "Demo Snack Wholesale",
    "Demo Snack Wholesale": "Example Dry Goods Wholesale",
    "Demo Beverage Distributors": "Example Dry Goods Wholesale",
}

# group -> category -> (subcategory, vendor, price range, taxable, [(base name, [sizes])])
CATALOG = {
    "PRODUCE": {
        "Fruit": ("Fresh", "Demo Produce Supply", (0.79, 5.99), False, [
            ("Bananas", ["per lb"]), ("Gala Apples", ["per lb", "3 lb bag"]), ("Navel Oranges", ["per lb", "4 lb bag"]),
            ("Mangoes", ["each"]), ("Red Grapes", ["per lb"]), ("Lemons", ["each", "2 lb bag"]),
            ("Limes", ["each"]), ("Strawberries", ["1 lb"]), ("Blueberries", ["6 oz", "18 oz"]),
            ("Pineapple", ["each"]), ("Watermelon", ["each"]), ("Pears", ["per lb"]),
        ]),
        "Vegetables": ("Fresh", "Demo Produce Supply", (0.59, 4.99), False, [
            ("Yellow Onions", ["per lb", "3 lb bag"]), ("Red Onions", ["per lb"]), ("Russet Potatoes", ["5 lb bag"]),
            ("Tomatoes", ["per lb"]), ("Cucumbers", ["each"]), ("Green Peppers", ["each"]),
            ("Carrots", ["2 lb bag"]), ("Broccoli", ["per lb"]), ("Spinach", ["10 oz"]),
            ("Romaine Lettuce", ["each"]), ("Garlic", ["each", "1 lb jar"]), ("Ginger Root", ["per lb"]),
            ("Cilantro", ["bunch"]), ("Green Beans", ["per lb"]), ("Sweet Potatoes", ["per lb"]),
        ]),
    },
    "DAIRY": {
        "Milk": ("Fluid milk", "Sample Dairy Distributors", (1.99, 5.49), False, [
            ("Whole Milk", ["1/2 gal", "1 gal"]), ("2% Milk", ["1/2 gal", "1 gal"]), ("Skim Milk", ["1/2 gal"]),
            ("Oat Drink", ["64 oz"]), ("Almond Drink", ["64 oz"]),
        ]),
        "Cheese": ("Cheese", "Sample Dairy Distributors", (2.99, 8.99), False, [
            ("Cheddar Block", ["8 oz", "16 oz"]), ("Shredded Mozzarella", ["8 oz", "32 oz"]),
            ("Paneer", ["14 oz"]), ("Feta Crumbles", ["6 oz"]), ("Sliced Swiss", ["8 oz"]),
        ]),
        "Yogurt": ("Cultured", "Sample Dairy Distributors", (0.99, 6.49), False, [
            ("Plain Yogurt", ["32 oz"]), ("Greek Yogurt", ["5.3 oz", "32 oz"]), ("Strawberry Yogurt", ["6 oz"]),
            ("Drinkable Yogurt", ["32 oz"]),
        ]),
        "Eggs": ("Eggs", "Sample Dairy Distributors", (2.49, 6.99), False, [
            ("Large Eggs", ["dozen", "18 ct"]), ("Brown Eggs", ["dozen"]),
        ]),
        "Butter": ("Butter", "Sample Dairy Distributors", (3.49, 7.99), False, [
            ("Salted Butter", ["16 oz"]), ("Unsalted Butter", ["16 oz"]), ("Ghee", ["16 oz", "32 oz"]),
        ]),
    },
    "GROCERY": {
        "Rice": ("Grains", "Example Dry Goods Wholesale", (2.49, 24.99), False, [
            ("Basmati Rice", ["2 lb", "10 lb", "20 lb"]), ("Jasmine Rice", ["5 lb", "20 lb"]),
            ("Brown Rice", ["2 lb"]), ("Sona Masoori Rice", ["10 lb", "20 lb"]),
        ]),
        "Flour": ("Baking", "Example Dry Goods Wholesale", (2.99, 19.99), False, [
            ("All Purpose Flour", ["5 lb"]), ("Whole Wheat Atta", ["10 lb", "20 lb"]), ("Chickpea Flour", ["2 lb", "4 lb"]),
            ("Rice Flour", ["2 lb"]),
        ]),
        "Lentils & Beans": ("Pulses", "Example Dry Goods Wholesale", (1.49, 9.99), False, [
            ("Red Lentils", ["2 lb", "4 lb"]), ("Yellow Split Peas", ["2 lb"]), ("Chickpeas", ["2 lb", "4 lb"]),
            ("Black Beans", ["2 lb"]), ("Kidney Beans", ["2 lb"]), ("Mung Beans", ["2 lb"]),
            ("Canned Chickpeas", ["15 oz"]), ("Canned Black Beans", ["15 oz"]),
        ]),
        "Spices": ("Spices", "Example Dry Goods Wholesale", (1.29, 8.99), False, [
            ("Ground Turmeric", ["3.5 oz", "7 oz"]), ("Ground Cumin", ["3.5 oz", "7 oz"]), ("Cumin Seeds", ["7 oz"]),
            ("Ground Coriander", ["7 oz"]), ("Chili Powder", ["3.5 oz", "7 oz"]), ("Garam Masala", ["3.5 oz"]),
            ("Black Pepper", ["4 oz"]), ("Cinnamon Sticks", ["3.5 oz"]), ("Cardamom Pods", ["3.5 oz"]),
            ("Mustard Seeds", ["7 oz"]), ("Paprika", ["4 oz"]), ("Bay Leaves", ["1 oz"]),
        ]),
        "Oil & Vinegar": ("Cooking oil", "Example Dry Goods Wholesale", (2.99, 29.99), False, [
            ("Vegetable Oil", ["48 oz", "1 gal"]), ("Olive Oil", ["16 oz", "34 oz"]), ("Mustard Oil", ["1 L"]),
            ("Coconut Oil", ["14 oz"]), ("White Vinegar", ["32 oz"]), ("Apple Cider Vinegar", ["32 oz"]),
        ]),
        "Pasta & Noodles": ("Pasta", "Example Dry Goods Wholesale", (0.99, 4.99), False, [
            ("Spaghetti", ["16 oz"]), ("Penne", ["16 oz"]), ("Elbow Macaroni", ["16 oz"]),
            ("Rice Noodles", ["14 oz"]), ("Instant Noodles", ["3 oz", "5 pack"]),
        ]),
        "Canned Goods": ("Canned", "Example Dry Goods Wholesale", (0.89, 4.49), False, [
            ("Diced Tomatoes", ["14.5 oz", "28 oz"]), ("Tomato Paste", ["6 oz"]), ("Coconut Milk", ["13.5 oz"]),
            ("Sweet Corn", ["15 oz"]), ("Chicken Broth", ["32 oz"]), ("Tuna in Water", ["5 oz"]),
        ]),
        "Breakfast": ("Cereal", "Example Dry Goods Wholesale", (2.49, 6.99), False, [
            ("Rolled Oats", ["18 oz", "42 oz"]), ("Corn Flakes", ["12 oz"]), ("Honey Granola", ["12 oz"]),
            ("Pancake Mix", ["32 oz"]),
        ]),
        "Condiments": ("Sauces", "Example Dry Goods Wholesale", (1.49, 6.99), False, [
            ("Tomato Ketchup", ["20 oz"]), ("Yellow Mustard", ["14 oz"]), ("Mayonnaise", ["30 oz"]),
            ("Mango Chutney", ["12 oz"]), ("Mixed Pickle", ["10 oz"]), ("Hot Sauce", ["5 oz"]), ("Soy Sauce", ["15 oz"]),
            ("Honey", ["12 oz"]), ("Peanut Butter", ["16 oz"]), ("Strawberry Jam", ["18 oz"]),
        ]),
        "Tea & Coffee": ("Hot drinks", "Example Dry Goods Wholesale", (2.99, 12.99), False, [
            ("Black Tea Bags", ["100 ct"]), ("Green Tea Bags", ["40 ct"]), ("Loose Leaf Tea", ["16 oz"]),
            ("Ground Coffee", ["12 oz"]), ("Instant Coffee", ["7 oz"]),
        ]),
        "Sugar & Sweeteners": ("Baking", "Example Dry Goods Wholesale", (1.99, 8.99), False, [
            ("Granulated Sugar", ["4 lb"]), ("Brown Sugar", ["2 lb"]), ("Jaggery", ["2 lb"]),
        ]),
    },
    "BEVERAGES": {
        "Soft Drinks": ("Soda", "Demo Beverage Distributors", (0.99, 7.99), True, [
            ("Cola", ["12 oz can", "2 L", "12 pack"]), ("Lemon Lime Soda", ["2 L", "12 pack"]), ("Ginger Ale", ["2 L"]),
            ("Root Beer", ["2 L"]), ("Orange Soda", ["2 L"]),
        ]),
        "Juice": ("Juice", "Demo Beverage Distributors", (1.99, 5.99), True, [
            ("Orange Juice", ["52 oz"]), ("Apple Juice", ["64 oz"]), ("Mango Nectar", ["33.8 oz"]),
            ("Cranberry Juice", ["64 oz"]), ("Coconut Water", ["16.9 oz"]),
        ]),
        "Water": ("Water", "Demo Beverage Distributors", (0.99, 6.99), True, [
            ("Spring Water", ["16.9 oz", "24 pack"]), ("Sparkling Water", ["12 pack"]),
        ]),
    },
    "SNACKS": {
        "Chips": ("Chips", "Demo Snack Wholesale", (1.29, 4.99), True, [
            ("Salted Potato Chips", ["8 oz"]), ("Tortilla Chips", ["13 oz"]), ("Plantain Chips", ["5 oz"]),
            ("Spicy Mix", ["14 oz"]), ("Banana Chips", ["7 oz"]),
        ]),
        "Cookies": ("Cookies", "Demo Snack Wholesale", (1.49, 5.49), True, [
            ("Butter Cookies", ["12 oz"]), ("Chocolate Chip Cookies", ["13 oz"]), ("Cardamom Rusk", ["7 oz"]),
            ("Coconut Cookies", ["7 oz"]),
        ]),
        "Crackers": ("Crackers", "Demo Snack Wholesale", (1.99, 4.49), True, [
            ("Saltine Crackers", ["16 oz"]), ("Wheat Crackers", ["9 oz"]), ("Rice Crackers", ["3.5 oz"]),
        ]),
        "Nuts & Dried Fruit": ("Nuts", "Demo Snack Wholesale", (2.99, 14.99), True, [
            ("Roasted Almonds", ["8 oz", "2 lb"]), ("Cashews", ["8 oz", "2 lb"]), ("Raisins", ["12 oz"]),
            ("Pistachios", ["8 oz"]), ("Dates", ["16 oz"]), ("Mixed Nuts", ["16 oz"]),
        ]),
        "Candy": ("Candy", "Demo Snack Wholesale", (0.99, 4.99), True, [
            ("Milk Chocolate Bar", ["3.5 oz"]), ("Dark Chocolate Bar", ["3.5 oz"]), ("Gummy Bears", ["5 oz"]),
            ("Hard Candy Mix", ["10 oz"]),
        ]),
    },
    "BAKERY": {
        "Bread": ("Bread", "Demo Bakery Supply", (1.99, 5.99), False, [
            ("White Sandwich Bread", ["20 oz"]), ("Whole Wheat Bread", ["20 oz"]), ("Naan", ["4 ct"]),
            ("Pita Bread", ["6 ct"]), ("Hamburger Buns", ["8 ct"]), ("Bagels", ["6 ct"]),
        ]),
        "Tortillas": ("Flatbread", "Demo Bakery Supply", (1.99, 4.99), False, [
            ("Flour Tortillas", ["10 ct"]), ("Corn Tortillas", ["30 ct"]), ("Frozen Paratha", ["5 ct"]),
        ]),
        "Pastries": ("Sweet", "Demo Bakery Supply", (1.49, 7.99), False, [
            ("Croissants", ["4 ct"]), ("Blueberry Muffins", ["4 ct"]), ("Pound Cake", ["16 oz"]),
        ]),
    },
    "FROZEN": {
        "Frozen Vegetables": ("Vegetables", "Placeholder Frozen Foods", (1.49, 4.99), False, [
            ("Frozen Peas", ["16 oz"]), ("Frozen Mixed Vegetables", ["16 oz"]), ("Frozen Spinach", ["10 oz"]),
            ("Frozen Okra", ["12 oz"]), ("Frozen Corn", ["16 oz"]),
        ]),
        "Frozen Meals": ("Meals", "Placeholder Frozen Foods", (2.99, 8.99), False, [
            ("Vegetable Samosas", ["10 ct"]), ("Cheese Pizza", ["12 in"]), ("Vegetable Dumplings", ["16 oz"]),
            ("Bean Burritos", ["4 ct"]), ("Paneer Tikka Bites", ["12 oz"]),
        ]),
        "Ice Cream": ("Desserts", "Placeholder Frozen Foods", (2.99, 6.99), True, [
            ("Vanilla Ice Cream", ["48 oz"]), ("Mango Ice Cream", ["16 oz"]), ("Chocolate Ice Cream", ["48 oz"]),
            ("Ice Pops", ["12 ct"]),
        ]),
    },
    "HOUSEHOLD": {
        "Cleaning": ("Cleaning", "Demo Household Supply", (1.99, 12.99), True, [
            ("Dish Soap", ["24 oz"]), ("Laundry Detergent", ["50 oz", "100 oz"]), ("All Purpose Cleaner", ["32 oz"]),
            ("Sponges", ["6 ct"]), ("Trash Bags", ["30 ct"]), ("Bleach", ["64 oz"]),
        ]),
        "Paper Goods": ("Paper", "Demo Household Supply", (1.49, 19.99), True, [
            ("Paper Towels", ["6 roll"]), ("Bath Tissue", ["12 roll"]), ("Paper Plates", ["50 ct"]),
            ("Napkins", ["250 ct"]), ("Aluminum Foil", ["75 sq ft"]),
        ]),
        "Personal Care": ("Personal", "Demo Household Supply", (1.29, 9.99), True, [
            ("Bar Soap", ["4 pack"]), ("Shampoo", ["12 oz"]), ("Toothpaste", ["6 oz"]),
            ("Hand Sanitizer", ["8 oz"]), ("Coconut Hair Oil", ["8 oz"]),
        ]),
    },
    "DELI": {
        "Prepared Foods": ("Hot case", "Demo Bakery Supply", (3.99, 11.99), True, [
            ("Vegetable Biryani", ["per lb"]), ("Chana Masala", ["16 oz"]), ("Rotisserie Chicken", ["each"]),
            ("Garden Salad", ["each"]), ("Hummus", ["10 oz"]), ("Veggie Wrap", ["each"]),
        ]),
    },
}

# Staff logins (roles, not people). (username, is_admin, can_assign, team names)
STAFF = [
    ("store_manager", True, True, ["Managers"]),
    ("assistant_manager", False, True, ["Managers", "Grocery"]),
    ("grocery_lead", False, True, ["Grocery"]),
    ("produce_lead", False, True, ["Grocery"]),
    ("deli_lead", False, True, ["Kitchen"]),
    ("cashier_one", False, False, ["Grocery"]),
    ("cashier_two", False, False, ["Grocery"]),
    ("stock_clerk", False, False, ["Grocery"]),
    ("deli_cook", False, False, ["Kitchen"]),
]

# The App Review login (DEMO_USERNAMES must include it; DEMO_MODE treats every
# login as a demo login anyway).
REVIEW_USERNAME = "appreview"

CHAT_LINES = {
    "Everyone": [
        "Truck from the produce vendor is here - two people to the back please.",
        "Reminder: weekend sale signs go up Friday night.",
        "Cooler 2 was making a noise this morning, keeping an eye on it.",
        "Thanks everyone for a smooth Saturday!",
        "New price tags for rice are printed and on the manager desk.",
        "Please log any damaged items on the shrink sheet.",
        "Holiday hours are posted by the front door.",
    ],
    "Grocery": [
        "Aisle 3 needs facing before close.",
        "We are low on basmati 10 lb - added to the cart.",
        "Spice wall restocked.",
        "Can someone check the dates on the yogurt?",
        "Snack order went out, delivery Wednesday.",
        "Moved the canned goods endcap to aisle 5.",
    ],
    "Kitchen": [
        "Biryani batch is ready for the hot case.",
        "Slicer cleaned and covered.",
        "We need more deli containers - 16 oz.",
        "Hot case temp checked at 2 pm, all good.",
    ],
    "Managers": [
        "Weekly numbers look good - produce up a bit.",
        "Please review the open invoices before Friday.",
        "Schedule for next week is posted.",
        "Fix Issues list has a few negative counts to look at.",
    ],
}

TASK_TITLES = [
    ("Face aisles 1-4", "Grocery"),
    ("Check yogurt and milk dates", "Grocery"),
    ("Restock spice wall", "Grocery"),
    ("Clean produce misters", "Grocery"),
    ("Count cash drawer at shift change", "Managers"),
    ("Prep hot case for lunch", "Kitchen"),
    ("Deep clean deli slicer", "Kitchen"),
    ("Rotate frozen samosas (first in, first out)", "Grocery"),
    ("Update weekend sale signs", "Managers"),
    ("Break down cardboard in the back", "Grocery"),
    ("Check cooler temperatures", "Kitchen"),
    ("Review open invoices", "Managers"),
]

ANNOUNCEMENTS = [
    ("Welcome to StoreOps Demo Market", "This is a demo store with made-up data. Look around!"),
    ("Weekend sale", "Rice, lentils and spices are 10% off Saturday and Sunday."),
    ("Safety reminder", "Use the step stool for top shelves - never climb the racks."),
]
