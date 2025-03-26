from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import json
import os
from datetime import datetime, timedelta
import random
import hashlib
from functools import wraps

# app declaration
app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Change this to a secure random key in production

# Load data
def load_data(filename):
    with open(filename, 'r') as f:
        return json.load(f)

def save_data(data, filename):
    # Determine the correct path based on the filename
    if filename in ['products.json', 'locations.json']:
        filepath = f'../{filename}'
    else:
        filepath = filename
        
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2)

# Load initial data
products_data = load_data('../products.json')
locations_data = load_data('../locations.json')
users_data = load_data('users.json')
order_history = load_data('order_history.json')

# Authentication decorators
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('admin_login'))
        
        # Check if user is admin
        user = next((u for u in users_data['users'] if u['id'] == session['user_id']), None)
        if not user or not user.get('is_admin', False):
            return redirect(url_for('admin_login'))
            
        return f(*args, **kwargs)
    return decorated_function

# Helper functions
def get_user_by_id(user_id):
    return next((u for u in users_data['users'] if u['id'] == user_id), None)

def get_user_by_email(email):
    return next((u for u in users_data['users'] if u['email'] == email), None)

def get_product_by_id(product_id):
    for category in products_data['categories']:
        for product in category['products']:
            if product['id'] == product_id:
                return product
    return None

def get_category_by_id(category_id):
    return next((c for c in products_data['categories'] if c['id'] == category_id), None)

def get_city_by_id(city_id):
    return next((c for c in locations_data['cities'] if c['id'] == city_id), None)

def get_district_by_id(city_id, district_id):
    city = get_city_by_id(city_id)
    if city:
        return next((d for d in city['districts'] if d['id'] == district_id), None)
    return None

def get_location_info():
    if 'city_id' in session and 'district_id' in session:
        city = get_city_by_id(session['city_id'])
        district = get_district_by_id(session['city_id'], session['district_id'])
        if city and district:
            return {'city': city, 'district': district}
    return None

def calculate_price_with_location(base_price, location):
    if location:
        return round(base_price * location['district']['price_factor'])
    return base_price

def predict_price(product_id, location):
    """
    Predict price based on previous 10 days of orders for a specific product and location
    """
    if not location:
        return None
    
    # Load price history data
    try:
        with open('data/price_history.json', 'r') as f:
            price_history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # If file doesn't exist or is invalid, initialize with empty data
        price_history = {'history': []}
    
    # Create a unique key for product and location combination
    location_key = f"{location['city']['id']}_{location['district']['id']}"
    product_location_key = f"{product_id}_{location_key}"
    
    # Get product base price
    product = get_product_by_id(product_id)
    if not product:
        return None
    
    base_price = product['price']
    current_price = round(base_price * location['district']['price_factor'])
    
    # Filter price history for this product and location
    product_history = [
        entry for entry in price_history['history'] 
        if entry['product_id'] == product_id and entry['location_key'] == location_key
    ]
    
    # Sort by date (newest first)
    product_history.sort(key=lambda x: x['date'], reverse=True)
    
    # If we have less than 3 data points, return current price with location factor
    if len(product_history) < 3:
        return {
            'current_price': current_price,
            'predicted_price': current_price,
            'confidence': 80,  # Default confidence
            'history': []
        }
    
    # Use up to last 10 days of data for prediction
    recent_history = product_history[:10]
    
    # Calculate average price and trend
    prices = [entry['price'] for entry in recent_history]
    avg_price = sum(prices) / len(prices)
    
    # Simple trend analysis: positive if prices are generally increasing
    trend = sum(prices[:3]) / 3 - sum(prices[-3:]) / 3
    
    # Calculate predicted price with a small adjustment based on trend
    trend_factor = 0.02  # How much to adjust for trend
    predicted_price = avg_price * (1 + trend_factor * trend)
    
    # Make sure prediction is reasonable (within ±15% of current price)
    predicted_price = max(current_price * 0.85, min(current_price * 1.15, predicted_price))
    predicted_price = round(predicted_price)
    
    # Calculate confidence based on amount of data and volatility
    data_points = len(recent_history)
    volatility = sum([abs(prices[i] - prices[i-1]) for i in range(1, len(prices))]) / (len(prices) - 1)
    volatility_factor = volatility / avg_price
    
    confidence = round(min(95, max(50, 90 - volatility_factor * 100 + (data_points - 3) * 2)))
    
    # Format recent history for display
    formatted_history = []
    for entry in recent_history:
        date_obj = datetime.strptime(entry['date'], '%Y-%m-%d')
        formatted_history.append({
            'date': date_obj.strftime('%b %d'),
            'price': entry['price']
        })
    
    return {
        'current_price': current_price,
        'predicted_price': predicted_price,
        'confidence': confidence,
        'history': formatted_history
    }

def initialize_price_history():
    """
    Create initial price history data for the past 10 days (if it doesn't exist)
    """
    # Check if price history file already exists
    if os.path.exists('data/price_history.json'):
        return
    
    today = datetime.now()
    history_entries = []
    
    # For each product in each location, create 10 days of price history
    for category in products_data['categories']:
        for product in category['products']:
            product_id = product['id']
            base_price = product['price']
            
            for city in locations_data['cities']:
                for district in city['districts']:
                    location_key = f"{city['id']}_{district['id']}"
                    
                    # Calculate base price with location factor
                    location_price = round(base_price * district['price_factor'])
                    
                    # Create 10 days of slightly varying prices
                    for day_offset in range(10, 0, -1):
                        date = (today - timedelta(days=day_offset)).strftime('%Y-%m-%d')
                        
                        # Random variation between -5% and +5%
                        variation = 0.95 + (random.random() * 0.1)
                        price = round(location_price * variation)
                        
                        history_entries.append({
                            'product_id': product_id,
                            'location_key': location_key,
                            'date': date,
                            'price': price
                        })
    
    # Save the price history
    price_history = {'history': history_entries}
    with open('data/price_history.json', 'w') as f:
        json.dump(price_history, f, indent=2)
    
    print(f"Created initial price history with {len(history_entries)} entries")

def update_daily_price_history():
    """
    Update price history with today's prices for all products in all locations
    """
    today = datetime.now().strftime('%Y-%m-%d')
    
    # Load existing price history
    try:
        with open('data/price_history.json', 'r') as f:
            price_history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        price_history = {'history': []}
    
    # Check if we already have entries for today
    today_entries = [
        entry for entry in price_history['history'] 
        if entry['date'] == today
    ]
    
    if today_entries:
        print(f"Price history for {today} already exists ({len(today_entries)} entries)")
        return
    
    # Add today's prices for all products in all locations
    new_entries = []
    
    for category in products_data['categories']:
        for product in category['products']:
            product_id = product['id']
            base_price = product['price']
            
            for city in locations_data['cities']:
                for district in city['districts']:
                    location_key = f"{city['id']}_{district['id']}"
                    
                    # Calculate price with location factor
                    price = round(base_price * district['price_factor'])
                    
                    # Add a small random variation (-2% to +2%)
                    variation = 0.98 + (random.random() * 0.04)
                    price = round(price * variation)
                    
                    new_entries.append({
                        'product_id': product_id,
                        'location_key': location_key,
                        'date': today,
                        'price': price
                    })
    
    # Add new entries to history
    price_history['history'].extend(new_entries)
    
    # Optionally, limit history size to keep only recent data (e.g., last 30 days)
    if len(price_history['history']) > 100000:  # Arbitrary limit
        # Sort by date (oldest first)
        price_history['history'].sort(key=lambda x: x['date'])
        # Keep only the most recent entries
        price_history['history'] = price_history['history'][-100000:]
    
    # Save updated price history
    with open('data/price_history.json', 'w') as f:
        json.dump(price_history, f, indent=2)
    
    print(f"Added {len(new_entries)} price history entries for {today}")
    
# Initialize app data
def init_app_data():
    # Create data directory if it doesn't exist
    os.makedirs('data', exist_ok=True)
    
    # Initialize price history data if needed
    initialize_price_history()
    
    # Update today's price history
    update_daily_price_history()

# Call initialization when app starts
init_app_data()

# Routes
@app.route('/')
def index():
    location = get_location_info()
    
    # Apply location-based pricing to products
    categories = []
    for category in products_data['categories']:
        category_copy = category.copy()
        products_copy = []
        
        for product in category['products']:
            product_copy = product.copy()
            product_copy['original_price'] = product['price']
            product_copy['price'] = calculate_price_with_location(product['price'], location)
            products_copy.append(product_copy)
            
        category_copy['products'] = products_copy
        categories.append(category_copy)
    
    return render_template('index.html', 
                          categories=categories, 
                          location=location,
                          user_logged_in='user_id' in session,
                          user=get_user_by_id(session.get('user_id')))

@app.route('/category/<int:category_id>')
def category(category_id):
    category = get_category_by_id(category_id)
    location = get_location_info()
    
    if not category:
        return redirect(url_for('index'))
    
    # Apply location-based pricing to products
    category_copy = category.copy()
    products_copy = []
    
    for product in category['products']:
        product_copy = product.copy()
        product_copy['original_price'] = product['price']
        product_copy['price'] = calculate_price_with_location(product['price'], location)
        products_copy.append(product_copy)
        
    category_copy['products'] = products_copy
    
    return render_template('category.html', 
                          category=category_copy, 
                          location=location,
                          user_logged_in='user_id' in session,
                          user=get_user_by_id(session.get('user_id')))

@app.route('/product/<int:product_id>')
def product_detail(product_id):
    product = get_product_by_id(product_id)
    location = get_location_info()
    
    if not product:
        return redirect(url_for('index'))
    
    # Apply location-based pricing
    product_copy = product.copy()
    product_copy['original_price'] = product['price']
    product_copy['price'] = calculate_price_with_location(product['price'], location)
    
    # Get price prediction
    predicted_price = predict_price(product_id, location)
    
    return render_template('product.html', 
                          product=product_copy, 
                          location=location,
                          predicted_price=predicted_price,
                          user_logged_in='user_id' in session,
                          user=get_user_by_id(session.get('user_id')))

@app.route('/location')
def location_selection():
    current_city_id = session.get('city_id', 1)
    current_district_id = session.get('district_id', 101)
    
    return render_template('location.html', 
                          locations=locations_data,
                          current_city_id=current_city_id,
                          current_district_id=current_district_id,
                          location=get_location_info(),
                          user_logged_in='user_id' in session,
                          user=get_user_by_id(session.get('user_id')))

@app.route('/update-location', methods=['POST'])
def update_location():
    city_id = int(request.form.get('city_id', 1))
    district_id = int(request.form.get('district_id', 101))
    redirect_url = request.form.get('redirect_url', url_for('index'))
    
    session['city_id'] = city_id
    session['district_id'] = district_id
    
    return redirect(redirect_url)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        # Simple password hashing (use a proper hashing library in production)
        password_hash = hashlib.md5(password.encode()).hexdigest()
        
        user = get_user_by_email(email)
        if user and user['password_hash'] == password_hash:
            session['user_id'] = user['id']
            return redirect(url_for('index'))
        
        return render_template('login.html', 
                              error='Invalid email or password',
                              email=email,
                              location=get_location_info(),
                              user_logged_in='user_id' in session,
                              user=get_user_by_id(session.get('user_id')))
    
    return render_template('login.html', 
                          location=get_location_info(),
                          user_logged_in='user_id' in session,
                          user=get_user_by_id(session.get('user_id')))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        first_name = request.form.get('first_name')
        last_name = request.form.get('last_name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        # Create form data to pass back in case of error
        form_data = {
            'first_name': first_name,
            'last_name': last_name,
            'email': email,
            'phone': phone
        }
        
        # Validation
        if password != confirm_password:
            return render_template('register.html', 
                                  error='Passwords do not match',
                                  form_data=form_data,
                                  location=get_location_info(),
                                  user_logged_in='user_id' in session,
                                  user=get_user_by_id(session.get('user_id')))
        
        if get_user_by_email(email):
            return render_template('register.html', 
                                  error='Email already registered',
                                  form_data=form_data,
                                  location=get_location_info(),
                                  user_logged_in='user_id' in session,
                                  user=get_user_by_id(session.get('user_id')))
        
        # Create new user
        new_user = {
            'id': max(u['id'] for u in users_data['users']) + 1,
            'first_name': first_name,
            'last_name': last_name,
            'email': email,
            'phone': phone,
            'password_hash': hashlib.md5(password.encode()).hexdigest(),
            'address': '',
            'city_id': session.get('city_id', 1),
            'district_id': session.get('district_id', 101),
            'pincode': '',
            'joined_date': datetime.now().strftime('%Y-%m-%d'),
            'is_admin': False
        }
        
        users_data['users'].append(new_user)
        save_data(users_data, 'users.json')
        
        # Log in the new user
        session['user_id'] = new_user['id']
        return redirect(url_for('index'))
    
    return render_template('register.html', 
                          form_data={},
                          location=get_location_info(),
                          user_logged_in='user_id' in session,
                          user=get_user_by_id(session.get('user_id')))

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('index'))

@app.route('/add-to-cart', methods=['POST'])
@login_required
def add_to_cart():
    product_id = int(request.form.get('product_id'))
    quantity = int(request.form.get('quantity', 1))
    
    if 'cart' not in session:
        session['cart'] = []
    
    cart = session['cart']
    
    # Check if product already in cart
    for item in cart:
        if item['product_id'] == product_id:
            item['quantity'] += quantity
            session['cart'] = cart
            return redirect(request.referrer or url_for('index'))
    
    # Add new item to cart
    cart.append({
        'product_id': product_id,
        'quantity': quantity
    })
    
    session['cart'] = cart
    return redirect(request.referrer or url_for('index'))

@app.route('/update-cart', methods=['POST'])
@login_required
def update_cart():
    try:
        product_id = int(request.form.get('product_id'))
        quantity = int(request.form.get('quantity', 0))
    except ValueError:
        # Handle invalid input
        return redirect(url_for('cart'))
    
    if quantity < 0:
        quantity = 0  # Ensure quantity is not negative
    
    if 'cart' not in session:
        return redirect(url_for('cart'))
    
    cart = session['cart']
    
    if quantity == 0:
        # Remove item from cart
        cart = [item for item in cart if item['product_id'] != product_id]
    else:
        # Update quantity
        item_found = False
        for item in cart:
            if item['product_id'] == product_id:
                item['quantity'] = quantity
                item_found = True
                break
        
        # If item not found but quantity > 0, add it
        if not item_found and quantity > 0:
            cart.append({
                'product_id': product_id,
                'quantity': quantity
            })
    
    session['cart'] = cart
    return redirect(url_for('cart'))

@app.route('/cart')
def cart():
    location = get_location_info()
    cart_items = []
    total = 0
    original_total = 0
    
    if 'cart' in session:
        for item in session['cart']:
            product = get_product_by_id(item['product_id'])
            if product:
                price = calculate_price_with_location(product['price'], location)
                subtotal = price * item['quantity']
                original = product['price'] * item['quantity']
                
                cart_items.append({
                    'id': product['id'],
                    'name': product['name'],
                    'image': product['image'],
                    'unit': product['unit'],
                    'price': price,
                    'quantity': item['quantity'],
                    'subtotal': subtotal,
                    'original': original
                })
                
                total += subtotal
                original_total += original
    
    return render_template('cart.html', 
                          cart_items=cart_items, 
                          total=total,
                          original_total=original_total,
                          location=location,
                          user_logged_in='user_id' in session,
                          user=get_user_by_id(session.get('user_id')))

@app.route('/clear-cart')
def clear_cart():
    session.pop('cart', None)
    return redirect(url_for('cart'))

@app.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    user = get_user_by_id(session['user_id'])
    location = get_location_info()
    
    if not location:
        flash('Please select a delivery location', 'error')
        return redirect(url_for('location_selection', redirect_url=url_for('checkout')))
    
    if 'cart' not in session or not session['cart']:
        flash('Your cart is empty', 'error')
        return redirect(url_for('cart'))
    
    # Calculate total and prepare cart items
    cart_items = []
    total = 0
    
    for item in session['cart']:
        product = get_product_by_id(item['product_id'])
        if product:
            price = calculate_price_with_location(product['price'], location)
            subtotal = price * item['quantity']
            
            cart_items.append({
                'id': product['id'],
                'name': product['name'],
                'image': product['image'],
                'unit': product['unit'],
                'price': price,
                'quantity': item['quantity'],
                'subtotal': subtotal
            })
            
            total += subtotal
    
    if request.method == 'POST':
        # Create a new order
        order_id = max(order['id'] for order in order_history['orders']) + 1 if order_history['orders'] else 1
        
        new_order = {
            'id': order_id,
            'user_id': user['id'],
            'date': datetime.now().strftime('%Y-%m-%d'),
            'location': {
                'city_id': location['city']['id'],
                'district_id': location['district']['id']
            },
            'items': [],
            'total': total,
            'status': 'pending',
            'payment_method': request.form.get('payment_method', 'cod')
        }
        
        # Add items to order
        for item in cart_items:
            new_order['items'].append({
                'product_id': item['id'],
                'name': item['name'],
                'price': item['price'],
                'quantity': item['quantity'],
                'subtotal': item['subtotal']
            })
            
            # Update inventory (if implemented)
            # update_inventory(item['id'], location['city']['id'], location['district']['id'], -item['quantity'])
        
        # Add order to history
        order_history['orders'].append(new_order)
        save_data(order_history, 'order_history.json')
        
        # Clear cart
        session.pop('cart', None)
        
        # Redirect to order confirmation
        flash('Order placed successfully!', 'success')
        return redirect(url_for('index'))
    
    return render_template('checkout.html', 
                          total=total,
                          cart_items=cart_items,
                          user=user,
                          location=location,
                          user_logged_in=True)

@app.route('/api/cart_count')
def cart_count():
    count = 0
    if 'cart' in session:
        for item in session['cart']:
            count += item['quantity']
    
    return jsonify({'count': count})

# Admin routes
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Simple password hashing (use a proper hashing library in production)
        password_hash = hashlib.md5(password.encode()).hexdigest()
        
        # Find admin user
        admin_user = next((u for u in users_data['users'] if u['email'] == username and u['is_admin']), None)
        
        if admin_user and admin_user['password_hash'] == password_hash:
            session['user_id'] = admin_user['id']
            return redirect(url_for('admin_dashboard'))
        
        return render_template('admin_login.html', 
                              error='Invalid credentials',
                              username=username)
    
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('user_id', None)
    return redirect(url_for('admin_login'))

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    admin_user = get_user_by_id(session['user_id'])
    
    # Mock statistics for dashboard
    stats = {
        'total_orders': len(order_history['orders']),
        'orders_today': random.randint(5, 20),
        'total_revenue': sum(order['total'] for order in order_history['orders']),
        'revenue_today': random.randint(1000, 5000),
        'total_users': len(users_data['users']),
        'new_users_today': random.randint(1, 10)
    }
    
    # Recent orders
    recent_orders = []
    for order in sorted(order_history['orders'], key=lambda x: x['date'], reverse=True)[:5]:
        user = get_user_by_id(order['user_id'])
        recent_orders.append({
            'id': order['id'],
            'user_name': f"{user['first_name']} {user['last_name']}",
            'date': order['date'],
            'amount': order['total']
        })
    
    # Price predictions
    price_predictions = []
    for i in range(5):
        product = get_product_by_id(random.choice([101, 102, 201, 301]))
        city = get_city_by_id(random.choice([1, 2, 3]))
        district = random.choice(city['districts'])
        location = {'city': city, 'district': district}
        
        predicted_price = predict_price(product['id'], location)
        current_price = calculate_price_with_location(product['price'], location)
        
        price_predictions.append({
            'name': product['name'],
            'location': f"{city['name']}, {district['name']}",
            'predicted_price': predicted_price or current_price,
            'current_price': current_price
        })
    
    return render_template('admin_dashboard.html',
                          admin_user=admin_user,
                          stats=stats,
                          recent_orders=recent_orders,
                          price_predictions=price_predictions)

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = get_user_by_id(session['user_id'])
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'update_profile':
            # Update user info
            user['first_name'] = request.form.get('first_name')
            user['last_name'] = request.form.get('last_name')
            user['phone'] = request.form.get('phone')
            user['address'] = request.form.get('address')
            user['pincode'] = request.form.get('pincode')
            
            # Update location if provided
            if request.form.get('city_id') and request.form.get('district_id'):
                user['city_id'] = int(request.form.get('city_id'))
                user['district_id'] = int(request.form.get('district_id'))
            
            # Save changes
            save_data(users_data, 'users.json')
            flash('Profile updated successfully', 'success')
        
        elif action == 'change_password':
            current_password = request.form.get('current_password')
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')
            
            # Verify current password
            current_hash = hashlib.md5(current_password.encode()).hexdigest()
            if user['password_hash'] != current_hash:
                flash('Current password is incorrect', 'error')
            elif new_password != confirm_password:
                flash('New passwords do not match', 'error')
            else:
                # Update password
                user['password_hash'] = hashlib.md5(new_password.encode()).hexdigest()
                save_data(users_data, 'users.json')
                flash('Password changed successfully', 'success')
                
        return redirect(url_for('profile'))
    
    return render_template('profile.html', 
                          user=user,
                          location=get_location_info(),
                          locations=locations_data,
                          user_logged_in=True)

# Add this function to manage inventory
def update_inventory(product_id, city_id, district_id, quantity):
    for category in products_data['categories']:
        for product in category['products']:
            if product['id'] == product_id:
                if 'inventory' not in product:
                    product['inventory'] = {}
                key = f"{city_id}_{district_id}"
                product['inventory'][key] = quantity
                save_data(products_data, 'products.json')
                return True
    return False

# Modify the admin_products route to include inventory management
@app.route('/admin/products', methods=['GET', 'POST'])
@admin_required
def admin_products():
    admin_user = get_user_by_id(session['user_id'])
    message = None
    message_type = None
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add_product':
            try:
                # Get the highest existing product ID and add 1
                max_id = 0
                for category in products_data['categories']:
                    for product in category['products']:
                        if product['id'] > max_id:
                            max_id = product['id']
                
                new_product = {
                    'id': max_id + 1,
                    'name': request.form.get('name'),
                    'price': float(request.form.get('price')),
                    'unit': request.form.get('unit'),
                    'image': request.form.get('image', '/placeholder.svg?height=200&width=200'),
                    'description': request.form.get('description', ''),
                    'inventory': {}  # Initialize empty inventory
                }
                
                category_id = int(request.form.get('category_id'))
                category_found = False
                
                for category in products_data['categories']:
                    if category['id'] == category_id:
                        category['products'].append(new_product)
                        category_found = True
                        break
                
                if not category_found:
                    message = f"Category ID {category_id} not found"
                    message_type = "error"
                else:
                    save_data(products_data, 'products.json')
                    message = 'Product added successfully'
                    message_type = 'success'
            except Exception as e:
                message = f"Error adding product: {str(e)}"
                message_type = "error"
        
        elif action == 'edit_product':
            try:
                product_id = int(request.form.get('product_id'))
                product_name = request.form.get('name')
                product_price = float(request.form.get('price'))
                product_unit = request.form.get('unit')
                product_image = request.form.get('image')
                product_description = request.form.get('description', '')
                category_id = int(request.form.get('category_id'))
                
                # Find the product to update
                product_found = False
                old_category_id = None
                
                # First find the product and its current category
                for category in products_data['categories']:
                    for i, product in enumerate(category['products']):
                        if product['id'] == product_id:
                            old_category_id = category['id']
                            if old_category_id == category_id:
                                # Update in the same category
                                product['name'] = product_name
                                product['price'] = product_price
                                product['unit'] = product_unit
                                product['image'] = product_image
                                product['description'] = product_description
                                product_found = True
                            else:
                                # Remove from current category to move to the new one
                                removed_product = category['products'].pop(i)
                                # Preserve inventory data
                                if 'inventory' in removed_product:
                                    removed_product['inventory'] = removed_product['inventory']
                            break
                    if old_category_id:
                        break
                
                # If product is being moved to a different category, add it to the new one
                if old_category_id != category_id and old_category_id is not None:
                    for category in products_data['categories']:
                        if category['id'] == category_id:
                            # Create updated product with the same ID
                            updated_product = {
                                'id': product_id,
                                'name': product_name,
                                'price': product_price,
                                'unit': product_unit,
                                'image': product_image,
                                'description': product_description,
                                'inventory': removed_product.get('inventory', {})
                            }
                            category['products'].append(updated_product)
                            product_found = True
                            break
                
                if product_found:
                    save_data(products_data, 'products.json')
                    message = 'Product updated successfully'
                    message_type = 'success'
                else:
                    message = f"Product ID {product_id} not found"
                    message_type = "error"
            except Exception as e:
                message = f"Error updating product: {str(e)}"
                message_type = "error"
        
        elif action == 'update_inventory':
            try:
                product_id = int(request.form.get('product_id'))
                city_id = int(request.form.get('city_id'))
                district_id = int(request.form.get('district_id'))
                quantity = int(request.form.get('quantity', 0))
                
                if update_inventory(product_id, city_id, district_id, quantity):
                    message = 'Inventory updated successfully'
                    message_type = 'success'
                else:
                    message = 'Failed to update inventory'
                    message_type = 'error'
            except Exception as e:
                message = f"Error updating inventory: {str(e)}"
                message_type = "error"
        
        elif action == 'add_category':
            try:
                # Get the highest existing category ID and add 1
                max_id = max(category['id'] for category in products_data['categories'])
                
                new_category = {
                    'id': max_id + 1,
                    'name': request.form.get('name'),
                    'image': request.form.get('image', '/placeholder.svg?height=80&width=80'),
                    'products': []
                }
                
                products_data['categories'].append(new_category)
                save_data(products_data, 'products.json')
                message = 'Category added successfully'
                message_type = 'success'
            except Exception as e:
                message = f"Error adding category: {str(e)}"
                message_type = "error"
    
    # Prepare products list with categories and inventory
    products = []
    for category in products_data['categories']:
        for product in category['products']:
            # Calculate predicted price range
            min_price = round(product['price'] * 0.8)
            max_price = round(product['price'] * 1.2)
            
            inventory = product.get('inventory', {})
            
            # Format inventory for display
            inventory_display = []
            for key, value in inventory.items():
                city_id, district_id = map(int, key.split('_'))
                city = get_city_by_id(city_id)
                district = get_district_by_id(city_id, district_id)
                if city and district:
                    inventory_display.append({
                        'location': f"{city['name']}, {district['name']}",
                        'city_id': city_id,
                        'district_id': district_id,
                        'quantity': value
                    })
            
            products.append({
                'id': product['id'],
                'name': product['name'],
                'image': product['image'],
                'category': category['name'],
                'category_id': category['id'],
                'price': product['price'],
                'unit': product.get('unit', ''),
                'description': product.get('description', ''),
                'min_predicted_price': min_price,
                'max_predicted_price': max_price,
                'inventory': inventory_display
            })
    
    # Get all cities and districts for the inventory form
    locations = []
    for city in locations_data['cities']:
        for district in city['districts']:
            locations.append({
                'city_id': city['id'],
                'district_id': district['id'],
                'name': f"{city['name']}, {district['name']}"
            })
    
    return render_template('admin_products.html',
                          admin_user=admin_user,
                          products=products,
                          categories=products_data['categories'],
                          locations=locations,
                          locations_data=locations_data,
                          message=message,
                          message_type=message_type)

# Add this route to handle product deletion
@app.route('/admin/products/delete/<int:product_id>', methods=['POST'])
@admin_required
def delete_product(product_id):
    # First check if the product exists in any orders
    product_in_orders = False
    for order in order_history['orders']:
        for item in order['items']:
            if item['product_id'] == product_id:
                product_in_orders = True
                break
        if product_in_orders:
            break
    
    if product_in_orders:
        flash('Cannot delete product that exists in order history', 'error')
        return redirect(url_for('admin_products'))
    
    # Now delete the product
    for category in products_data['categories']:
        for product in category['products']:
            if product['id'] == product_id:
                category['products'].remove(product)
                save_data(products_data, 'products.json')
                flash('Product deleted successfully', 'success')
                return redirect(url_for('admin_products'))
    
    flash('Product not found', 'error')
    return redirect(url_for('admin_products'))

@app.route('/admin/orders')
@admin_required
def admin_orders():
    admin_user = get_user_by_id(session['user_id'])
    
    # Prepare orders list
    orders = []
    for order in sorted(order_history['orders'], key=lambda x: x['date'], reverse=True):
        user = get_user_by_id(order['user_id'])
        city = get_city_by_id(order['location']['city_id'])
        district = get_district_by_id(order['location']['city_id'], order['location']['district_id'])
        
        orders.append({
            'id': order['id'],
            'customer_name': f"{user['first_name']} {user['last_name']}",
            'date': order['date'],
            'location': f"{city['name']}, {district['name']}",
            'items': sum(item['quantity'] for item in order['items']),
            'total': order['total'],
            'status': order.get('status', 'pending')
        })
    
    return render_template('admin_orders.html',
                          admin_user=admin_user,
                          orders=orders)

@app.route('/admin/users')
@admin_required
def admin_users():
    admin_user = get_user_by_id(session['user_id'])
    
    # Prepare users list
    users = []
    for user in users_data['users']:
        city = get_city_by_id(user['city_id'])
        district = get_district_by_id(user['city_id'], user['district_id'])
        
        # Count orders for this user
        orders_count = sum(1 for order in order_history['orders'] if order['user_id'] == user['id'])
        
        users.append({
            'id': user['id'],
            'name': f"{user['first_name']} {user['last_name']}",
            'email': user['email'],
            'phone': user['phone'],
            'location': f"{city['name']}, {district['name']}",
            'joined_date': user['joined_date'],
            'orders_count': orders_count,
            'is_admin': user['is_admin'],
            'is_blocked': user.get('is_blocked', False)
        })
    
    return render_template('admin_users.html',
                          admin_user=admin_user,
                          users=users)

@app.route('/admin/price-analytics')
@admin_required
def admin_price_analytics():
    admin_user = get_user_by_id(session['user_id'])
    
    # Mock statistics for price analytics
    stats = {
        'avg_fluctuation': 12.5,
        'most_volatile_product': 'Fresh Apples',
        'most_volatile_fluctuation': 18.3,
        'most_stable_location': 'Mumbai, Dadar',
        'most_stable_fluctuation': 5.2
    }
    
    # Mock predictions data
    predictions = []
    for i in range(10):
        product = get_product_by_id(random.choice([101, 102, 201, 301]))
        category = next(c['name'] for c in products_data['categories'] if product['id'] // 100 == c['id'])
        city = get_city_by_id(random.choice([1, 2, 3]))
        district = random.choice(city['districts'])
        
        base_price = product['price']
        predicted_price = round(base_price * (0.8 + random.random() * 0.4))
        actual_price = round(base_price * district['price_factor'])
        
        # Calculate accuracy
        if predicted_price == 0 or actual_price == 0:
            accuracy = 0
        else:
            accuracy = round(100 - abs(predicted_price - actual_price) / actual_price * 100)
        
        predictions.append({
            'product_name': product['name'],
            'category': category,
            'location': f"{city['name']}, {district['name']}",
            'base_price': base_price,
            'predicted_price': predicted_price,
            'actual_price': actual_price,
            'accuracy': accuracy
        })
    
    return render_template('admin_price_analytics.html',
                          admin_user=admin_user,
                          stats=stats,
                          predictions=predictions,
                          categories=products_data['categories'],
                          cities=locations_data['cities'])

@app.route('/orders')
@login_required
def orders():
    user = get_user_by_id(session['user_id'])
    
    # Get orders for the current user
    user_orders = []
    for order in sorted(order_history['orders'], key=lambda x: x['date'], reverse=True):
        if order['user_id'] == user['id']:
            # Get location info
            city = get_city_by_id(order['location']['city_id'])
            district = get_district_by_id(order['location']['city_id'], order['location']['district_id'])
            
            # Format order for display
            order_info = {
                'id': order['id'],
                'date': order['date'],
                'location': f"{city['name']}, {district['name']}",
                'items_count': len(order['items']),
                'total': order['total'],
                'status': order.get('status', 'pending'),
                'items': order['items']
            }
            user_orders.append(order_info)
    
    return render_template('orders.html', 
                          orders=user_orders,
                          location=get_location_info(),
                          user_logged_in=True,
                          user=user)

@app.route('/admin/manage-admins', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_manage_admins():
    message = None
    message_type = None
    admin_user = get_user_by_id(session['user_id'])
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        # Promote user to admin
        if action == 'promote_to_admin':
            try:
                user_id = int(request.form.get('user_id'))
                
                # Find the user and update admin status
                user = get_user_by_id(user_id)
                if user:
                    user['is_admin'] = True
                    save_data(users_data, 'users.json')
                    message = f"Successfully promoted {user['first_name']} {user['last_name']} to administrator"
                    message_type = "success"
                else:
                    message = "User not found"
                    message_type = "error"
                    
            except Exception as e:
                message = f"Error promoting user: {str(e)}"
                message_type = "error"
        
        # Revoke admin privileges
        elif action == 'revoke_admin':
            try:
                user_id = int(request.form.get('user_id'))
                
                # Prevent self-revocation
                if user_id == session['user_id']:
                    message = "You cannot revoke your own admin privileges"
                    message_type = "error"
                else:
                    # Find the user and update admin status
                    user = get_user_by_id(user_id)
                    if user:
                        user['is_admin'] = False
                        save_data(users_data, 'users.json')
                        message = f"Successfully revoked administrator privileges from {user['first_name']} {user['last_name']}"
                        message_type = "success"
                    else:
                        message = "User not found"
                        message_type = "error"
                        
            except Exception as e:
                message = f"Error revoking admin privileges: {str(e)}"
                message_type = "error"
        
        # Create new admin user
        elif action == 'create_admin':
            try:
                first_name = request.form.get('first_name')
                last_name = request.form.get('last_name')
                email = request.form.get('email')
                phone = request.form.get('phone')
                password = request.form.get('password')
                confirm_password = request.form.get('confirm_password')
                
                # Validate passwords match
                if password != confirm_password:
                    message = "Passwords do not match"
                    message_type = "error"
                else:
                    # Check if email already exists
                    existing_user = get_user_by_email(email)
                    
                    if existing_user:
                        message = "A user with this email already exists"
                        message_type = "error"
                    else:
                        # Get highest user ID
                        max_id = 0
                        for user in users_data['users']:
                            if user['id'] > max_id:
                                max_id = user['id']
                        
                        # Set default city/district if needed
                        default_city_id = 1  # Assuming city ID 1 exists
                        default_district_id = 1  # Assuming district ID 1 exists
                        
                        # Create new user
                        password_hash = hashlib.md5(password.encode()).hexdigest()
                        
                        new_user = {
                            'id': max_id + 1,
                            'first_name': first_name,
                            'last_name': last_name,
                            'email': email,
                            'phone': phone,
                            'password_hash': password_hash,
                            'address': '',
                            'pincode': '',
                            'city_id': default_city_id,
                            'district_id': default_district_id,
                            'is_admin': True,
                            'joined_date': datetime.now().strftime('%Y-%m-%d')
                        }
                        
                        users_data['users'].append(new_user)
                        
                        # Save updated users
                        save_data(users_data, 'users.json')
                        
                        message = f"Successfully created admin user: {first_name} {last_name}"
                        message_type = "success"
            
            except Exception as e:
                message = f"Error creating admin user: {str(e)}"
                message_type = "error"
    
    # Prepare user list for display
    users = []
    for user in users_data['users']:
        # Count orders for this user
        orders_count = sum(1 for order in order_history['orders'] if order['user_id'] == user['id'])
        
        # Get city and district
        city = get_city_by_id(user['city_id'])
        district = get_district_by_id(user['city_id'], user['district_id'])
        location = f"{city['name']}, {district['name']}" if city and district else "Not specified"
        
        users.append({
            'id': user['id'],
            'name': f"{user['first_name']} {user['last_name']}",
            'email': user['email'],
            'phone': user['phone'],
            'location': location,
            'joined_date': user['joined_date'],
            'orders_count': orders_count,
            'is_admin': user['is_admin']
        })
    
    # Sort users: admins first, then by name
    users.sort(key=lambda x: (not x['is_admin'], x['name'].lower()))
    
    return render_template('admin_manage_admins.html', 
                          users=users, 
                          admin_user=admin_user,
                          message=message,
                          message_type=message_type)

@app.route('/admin/price-history', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_price_history():
    admin_user = get_user_by_id(session['user_id'])
    message = None
    message_type = None
    
    # Get filter parameters
    product_id = request.args.get('product_id', type=int)
    city_id = request.args.get('city_id', type=int)
    district_id = request.args.get('district_id', type=int)
    days = request.args.get('days', default=10, type=int)
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add_price_entry':
            try:
                entry_product_id = int(request.form.get('product_id'))
                entry_city_id = int(request.form.get('city_id'))
                entry_district_id = int(request.form.get('district_id'))
                entry_price = int(request.form.get('price'))
                entry_date = request.form.get('date')
                
                # Validate product exists
                product = get_product_by_id(entry_product_id)
                if not product:
                    message = "Product not found"
                    message_type = "error"
                else:
                    # Load price history
                    try:
                        with open('data/price_history.json', 'r') as f:
                            price_history = json.load(f)
                    except (FileNotFoundError, json.JSONDecodeError):
                        price_history = {'history': []}
                    
                    # Create location key
                    location_key = f"{entry_city_id}_{entry_district_id}"
                    
                    # Check if entry for this date, product, and location already exists
                    for i, entry in enumerate(price_history['history']):
                        if (entry['product_id'] == entry_product_id and 
                            entry['location_key'] == location_key and 
                            entry['date'] == entry_date):
                            # Update existing entry
                            price_history['history'][i]['price'] = entry_price
                            message = f"Updated price history for {product['name']} on {entry_date}"
                            message_type = "success"
                            break
                    else:
                        # Add new entry
                        price_history['history'].append({
                            'product_id': entry_product_id,
                            'location_key': location_key,
                            'date': entry_date,
                            'price': entry_price
                        })
                        message = f"Added new price history for {product['name']} on {entry_date}"
                        message_type = "success"
                    
                    # Save updated price history
                    with open('data/price_history.json', 'w') as f:
                        json.dump(price_history, f, indent=2)
            
            except Exception as e:
                message = f"Error adding price entry: {str(e)}"
                message_type = "error"
        
        elif action == 'regenerate_history':
            try:
                days_to_generate = int(request.form.get('days_to_generate', 10))
                
                # Delete existing price history file
                if os.path.exists('data/price_history.json'):
                    os.remove('data/price_history.json')
                
                # Generate new history
                initialize_price_history()
                
                message = f"Successfully regenerated price history for the past {days_to_generate} days"
                message_type = "success"
            
            except Exception as e:
                message = f"Error regenerating price history: {str(e)}"
                message_type = "error"
    
    # Load price history
    try:
        with open('data/price_history.json', 'r') as f:
            price_history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        price_history = {'history': []}
    
    # Filter price history based on parameters
    filtered_history = price_history['history']
    
    if product_id:
        filtered_history = [entry for entry in filtered_history if entry['product_id'] == product_id]
    
    if city_id and district_id:
        location_key = f"{city_id}_{district_id}"
        filtered_history = [entry for entry in filtered_history if entry['location_key'] == location_key]
    
    # Limit to the specified number of days
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    filtered_history = [entry for entry in filtered_history if entry['date'] >= cutoff_date]
    
    # Sort by date (newest first)
    filtered_history.sort(key=lambda x: (x['product_id'], x['location_key'], x['date']), reverse=True)
    
    # Get product and location details for display
    display_history = []
    for entry in filtered_history[:500]:  # Limit to 500 entries for performance
        product = get_product_by_id(entry['product_id'])
        location_key_parts = entry['location_key'].split('_')
        city_id, district_id = int(location_key_parts[0]), int(location_key_parts[1])
        
        city = get_city_by_id(city_id)
        district = get_district_by_id(city_id, district_id)
        
        if product and city and district:
            display_history.append({
                'product_id': entry['product_id'],
                'product_name': product['name'],
                'category': next((c['name'] for c in products_data['categories'] 
                                if product['id'] // 100 == c['id']), "Unknown"),
                'location': f"{city['name']}, {district['name']}",
                'city_id': city_id,
                'district_id': district_id,
                'date': entry['date'],
                'price': entry['price'],
                'base_price': product['price'],
                'price_factor': district['price_factor'],
                'expected_price': round(product['price'] * district['price_factor'])
            })
    
    # Calculate statistics
    stats = {}
    if display_history:
        # Count total entries
        stats['total_entries'] = len(price_history['history'])
        stats['displayed_entries'] = len(display_history)
        
        # Count unique products and locations
        unique_products = len(set(entry['product_id'] for entry in price_history['history']))
        unique_locations = len(set(entry['location_key'] for entry in price_history['history']))
        stats['unique_products'] = unique_products
        stats['unique_locations'] = unique_locations
        
        # Get date range
        all_dates = [entry['date'] for entry in price_history['history']]
        stats['oldest_date'] = min(all_dates)
        stats['newest_date'] = max(all_dates)
        
        # Avg price deviation from expected
        deviations = []
        for entry in display_history:
            deviation_pct = (entry['price'] - entry['expected_price']) / entry['expected_price'] * 100
            deviations.append(deviation_pct)
        
        stats['avg_deviation'] = round(sum(deviations) / len(deviations), 2)
        stats['max_deviation'] = round(max(deviations), 2)
        stats['min_deviation'] = round(min(deviations), 2)
    
    return render_template('admin_price_history.html',
                          admin_user=admin_user,
                          price_history=display_history,
                          products=get_all_products(),
                          locations=locations_data,
                          stats=stats,
                          filter_product_id=product_id,
                          filter_city_id=city_id,
                          filter_district_id=district_id,
                          filter_days=days,
                          message=message,
                          message_type=message_type)

# Helper function to get all products for dropdowns
def get_all_products():
    all_products = []
    for category in products_data['categories']:
        for product in category['products']:
            all_products.append({
                'id': product['id'],
                'name': product['name'],
                'category': category['name']
            })
    return sorted(all_products, key=lambda x: x['name'])

if __name__ == '__main__':
    app.run(debug=True)

