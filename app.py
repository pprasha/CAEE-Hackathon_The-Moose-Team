from flask import Flask, jsonify, request, send_file
import json
import io
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

app = Flask(__name__)

# In-memory storage
cargo_requests = []
request_counter = 0
latest_load_plan = None  # Store the latest generated plan for ground crew view

# Item type presets with dimensions in meters and weight in kg
ITEM_PRESETS = {
    "Water Case (24 bottles)": {
        "weight": 18, "length": 0.45, "width": 0.30, "height": 0.25
    },
    "Dozen NP Food Cans": {
        "weight": 10, "length": 0.40, "width": 0.30, "height": 0.22
    },
    "First-Aid Kit": {
        "weight": 4, "length": 0.35, "width": 0.25, "height": 0.20
    },
    "Toilet Paper (12-Roll Pack)": {
        "weight": 3, "length": 0.40, "width": 0.30, "height": 0.25
    },
    "Sanitary Pads (20 Pack)": {
        "weight": 1, "length": 0.30, "width": 0.20, "height": 0.12
    },
    "Clothing Pack (Jacket + Undergarments)": {
        "weight": 5, "length": 0.45, "width": 0.35, "height": 0.25
    },
    "Blanket (Rolled)": {
        "weight": 2, "length": 0.50, "width": 0.25, "height": 0.25
    },
    "Pet Supplies Pack": {
        "weight": 6, "length": 0.50, "width": 0.30, "height": 0.30
    },
    "Baby Formula (Case)": {
        "weight": 8, "length": 0.40, "width": 0.30, "height": 0.25
    }
}

# Aircraft presets
AIRCRAFT_PRESETS = {
    "UH-60 Black Hawk": {"max_weight": 1200, "max_length": 3.8, "max_width": 2.2, "max_height": 1.3}
}

@app.route('/')
def index():
    return HTML_TEMPLATE

@app.route('/api/requests', methods=['GET', 'POST'])
def handle_requests():
    global request_counter, cargo_requests
    
    if request.method == 'POST':
        data = request.json
        item_type = data.get('item_type')
        quantity = int(data.get('quantity', 1))
        priority = int(data.get('priority', 1))
        
        if item_type not in ITEM_PRESETS:
            return jsonify({"error": "Invalid item type"}), 400
        
        item_specs = ITEM_PRESETS[item_type]
        
        for _ in range(quantity):
            request_counter += 1
            cargo_requests.append({
                "id": request_counter,
                "item_type": item_type,
                "priority": priority,
                "weight": item_specs["weight"],
                "length": item_specs["length"],
                "width": item_specs["width"],
                "height": item_specs["height"]
            })
        
        return jsonify({"success": True, "message": f"Added {quantity} {item_type}(s)"})
    
    return jsonify(cargo_requests)

@app.route('/api/requests/clear', methods=['POST'])
def clear_requests():
    global cargo_requests, request_counter
    cargo_requests = []
    request_counter = 0
    return jsonify({"success": True, "message": "All requests cleared"})

@app.route('/api/optimize', methods=['POST'])
def optimize_cargo():
    global latest_load_plan
    
    data = request.json
    max_weight = float(data.get('max_weight', 10000))
    max_length = float(data.get('max_length', 10))
    max_width = float(data.get('max_width', 3))
    max_height = float(data.get('max_height', 2.5))
    
    # Sort by priority (descending) then by weight (descending for better balancing)
    sorted_requests = sorted(
        cargo_requests,
        key=lambda x: (-x['priority'], -x['weight']),
        reverse=False
    )
    
    packed = []
    unpacked = []
    current_weight = 0
    current_volume = 0
    max_volume = max_length * max_width * max_height
    
    # Track positions for balanced loading
    # Divide cargo bay into quadrants for weight distribution
    front_left_weight = 0
    front_right_weight = 0
    rear_left_weight = 0
    rear_right_weight = 0
    
    # Use a more sophisticated packing with weight balancing
    for item in sorted_requests:
        item_volume = item['length'] * item['width'] * item['height']
        
        # Check if item fits within constraints
        if (current_weight + item['weight'] <= max_weight and
            current_volume + item_volume <= max_volume and
            item['length'] <= max_length and
            item['width'] <= max_width and
            item['height'] <= max_height):
            
            # Find available position with weight balancing
            best_position = find_balanced_position(
                packed, item, max_length, max_width, max_height, 
                front_left_weight, front_right_weight, 
                rear_left_weight, rear_right_weight
            )
            
            if best_position:
                item_with_pos = item.copy()
                item_with_pos['position'] = best_position
                packed.append(item_with_pos)
                current_weight += item['weight']
                current_volume += item_volume
                
                # Update quadrant weights
                in_front = best_position['x'] < max_length / 2
                on_left = best_position['y'] < max_width / 2
                
                if in_front and on_left:
                    front_left_weight += item['weight']
                elif in_front and not on_left:
                    front_right_weight += item['weight']
                elif not in_front and on_left:
                    rear_left_weight += item['weight']
                else:
                    rear_right_weight += item['weight']
            else:
                unpacked.append(item)
        else:
            unpacked.append(item)
    
    # Calculate final center of gravity and balance metrics
    if packed:
        cog_x = sum(p['position']['x'] * p['weight'] for p in packed) / current_weight
        cog_y = sum(p['position']['y'] * p['weight'] for p in packed) / current_weight
        cog_z = sum(p['position']['z'] * p['weight'] for p in packed) / current_weight
        
        # Calculate balance percentage (how close to center in both X and Y)
        balance_x = 100 - (abs(cog_x - max_length/2) / (max_length/2) * 100)
        balance_y = 100 - (abs(cog_y - max_width/2) / (max_width/2) * 100)
        
        balance_score = (balance_x + balance_y) / 2
        
        # Calculate left/right balance
        left_weight = front_left_weight + rear_left_weight
        right_weight = front_right_weight + rear_right_weight
    else:
        cog_x = cog_y = cog_z = 0
        balance_score = 100
        left_weight = right_weight = 0
        front_left_weight = front_right_weight = rear_left_weight = rear_right_weight = 0
    
    weight_utilization = (current_weight / max_weight * 100) if max_weight > 0 else 0
    volume_utilization = (current_volume / max_volume * 100) if max_volume > 0 else 0
    
    result = {
        "packed": packed,
        "unpacked": unpacked,
        "stats": {
            "total_weight": current_weight,
            "max_weight": max_weight,
            "weight_utilization": round(weight_utilization, 2),
            "total_volume": current_volume,
            "max_volume": max_volume,
            "volume_utilization": round(volume_utilization, 2),
            "items_packed": len(packed),
            "items_unpacked": len(unpacked),
            "center_of_gravity": {
                "x": round(cog_x, 2),
                "y": round(cog_y, 2),
                "z": round(cog_z, 2)
            },
            "balance_score": round(balance_score, 1),
            "left_weight": round(left_weight, 1),
            "right_weight": round(right_weight, 1)
        },
        "aircraft": {
            "type": "UH-60 Black Hawk",
            "max_length": max_length,
            "max_width": max_width,
            "max_height": max_height
        }
    }
    
    # Store for ground crew view
    latest_load_plan = result
    
    return jsonify(result)

def find_balanced_position(packed, item, max_length, max_width, max_height, 
                           front_left_weight, front_right_weight, 
                           rear_left_weight, rear_right_weight):
    """Find the best position for an item considering weight balance in all directions"""
    item_l = item['length']
    item_w = item['width']
    item_h = item['height']
    item_weight = item['weight']
    
    # Calculate which quadrant needs weight most
    total_weight = front_left_weight + front_right_weight + rear_left_weight + rear_right_weight
    
    if total_weight == 0:
        # First item - place in center-ish area
        target_quadrants = [(1, 1), (0, 1), (1, 0), (0, 0)]  # All quadrants equally
    else:
        # Find lightest quadrant
        quadrant_weights = {
            (0, 0): front_left_weight,   # Front-Left
            (0, 1): front_right_weight,  # Front-Right
            (1, 0): rear_left_weight,    # Rear-Left
            (1, 1): rear_right_weight    # Rear-Right
        }
        
        # Sort quadrants by weight (lightest first)
        target_quadrants = sorted(quadrant_weights.keys(), key=lambda q: quadrant_weights[q])
    
    # Try each quadrant in order of preference
    for rear, right in target_quadrants:
        # Define search area for this quadrant
        x_start = (max_length / 2) if rear else 0
        x_end = max_length if rear else (max_length / 2)
        y_start = (max_width / 2) if right else 0
        y_end = max_width if right else (max_width / 2)
        
        # Grid search within this quadrant
        step = 0.2  # 20cm steps for better performance
        
        for z in [i * step for i in range(int(max_height / step))]:
            if z + item_h > max_height:
                continue
                
            for y in [y_start + i * step for i in range(int((y_end - y_start) / step))]:
                if y + item_w > max_width:
                    continue
                    
                for x in [x_start + i * step for i in range(int((x_end - x_start) / step))]:
                    if x + item_l > max_length:
                        continue
                    
                    # Check position (center of item)
                    pos_x = x + item_l / 2
                    pos_y = y + item_w / 2
                    pos_z = z + item_h / 2
                    
                    # Check if this position overlaps with any packed item
                    overlaps = False
                    for p in packed:
                        if boxes_overlap(
                            x, y, z, item_l, item_w, item_h,
                            p['position']['x'] - p['length']/2,
                            p['position']['y'] - p['width']/2,
                            p['position']['z'] - p['height']/2,
                            p['length'], p['width'], p['height']
                        ):
                            overlaps = True
                            break
                    
                    if not overlaps:
                        return {'x': pos_x, 'y': pos_y, 'z': pos_z}
    
    # If no position found in any quadrant
    return None

def boxes_overlap(x1, y1, z1, l1, w1, h1, x2, y2, z2, l2, w2, h2):
    """Check if two boxes overlap"""
    return not (
        x1 + l1 <= x2 or x2 + l2 <= x1 or
        y1 + w1 <= y2 or y2 + w2 <= y1 or
        z1 + h1 <= z2 or z2 + h2 <= z1
    )

@app.route('/api/latest-plan', methods=['GET'])
def get_latest_plan():
    """API endpoint for ground crew to get the latest load plan"""
    if latest_load_plan:
        return jsonify(latest_load_plan)
    else:
        return jsonify({"error": "No load plan available yet"}), 404

@app.route('/api/export-pdf', methods=['POST'])
def export_pdf():
    data = request.json
    packed = data.get('packed', [])
    max_length = float(data.get('max_length', 3.8))
    max_width = float(data.get('max_width', 2.2))
    max_height = float(data.get('max_height', 1.3))
    stats = data.get('stats', {})
    
    # Generate PDF
    pdf_buffer = generate_loading_pdf(packed, max_length, max_width, max_height, stats)
    
    return send_file(
        pdf_buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name='loading_plan.pdf'
    )

def generate_loading_pdf(packed, max_length, max_width, max_height, stats):
    """Generate a 4-page PDF showing vertical slices of cargo bay"""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    
    # Define quarters (vertical slices along length)
    quarter_width = max_length / 4
    
    for quarter in range(4):
        # Calculate slice boundaries
        slice_start = quarter * quarter_width
        slice_end = (quarter + 1) * quarter_width
        
        # Filter items in this slice
        items_in_slice = []
        for item in packed:
            item_x = item['position']['x']
            item_length = item['length']
            item_start = item_x - item_length/2
            item_end = item_x + item_length/2
            
            # Check if item overlaps with this slice
            if item_start < slice_end and item_end > slice_start:
                items_in_slice.append(item)
        
        # Draw page header
        c.setFont("Helvetica-Bold", 20)
        c.drawString(50, height - 50, f"AirStack Loading Plan - Slice {quarter + 1} of 4")
        
        c.setFont("Helvetica", 12)
        c.drawString(50, height - 75, f"UH-60 Black Hawk")
        c.drawString(50, height - 92, f"Length Section: {slice_start:.2f}m - {slice_end:.2f}m")
        
        # Draw stats
        c.setFont("Helvetica", 10)
        c.drawString(400, height - 75, f"Total Weight: {stats.get('total_weight', 0):.1f} / {stats.get('max_weight', 0):.0f} kg")
        c.drawString(400, height - 92, f"Items in Slice: {len(items_in_slice)}")
        c.drawString(400, height - 109, f"Balance Score: {stats.get('balance_score', 0):.1f}%")
        
        cog = stats.get('center_of_gravity', {})
        c.drawString(400, height - 126, f"CoG: X:{cog.get('x', 0):.1f} Y:{cog.get('y', 0):.1f} Z:{cog.get('z', 0):.1f}m")
        
        # Draw cargo bay outline (top view)
        bay_draw_height = 400
        bay_draw_width = 500
        bay_x = 50
        bay_y = height - 550
        
        # Scale factors
        scale_w = bay_draw_width / max_width
        scale_h = bay_draw_height / max_height
        
        # Draw bay outline
        c.setStrokeColor(colors.black)
        c.setLineWidth(2)
        c.rect(bay_x, bay_y, bay_draw_width, bay_draw_height)
        
        # Add axis labels
        c.setFont("Helvetica", 10)
        c.drawString(bay_x + bay_draw_width/2 - 20, bay_y - 20, "Width (m)")
        c.saveState()
        c.translate(bay_x - 30, bay_y + bay_draw_height/2)
        c.rotate(90)
        c.drawString(-30, 0, "Height (m)")
        c.restoreState()
        
        # Draw grid
        c.setStrokeColor(colors.lightgrey)
        c.setLineWidth(0.5)
        for i in range(1, int(max_width) + 1):
            x = bay_x + i * scale_w
            c.line(x, bay_y, x, bay_y + bay_draw_height)
        for i in range(1, int(max_height) + 1):
            y = bay_y + i * scale_h
            c.line(bay_x, y, bay_x + bay_draw_width, y)
        
        # Draw items in this slice
        box_colors = [
            colors.red, colors.green, colors.blue, colors.yellow,
            colors.magenta, colors.cyan, colors.orange, colors.purple
        ]
        
        for idx, item in enumerate(items_in_slice):
            pos_y = item['position']['y']
            pos_z = item['position']['z']
            item_width = item['width']
            item_height = item['height']
            
            # Calculate box position (centered)
            box_x = bay_x + (pos_y - item_width/2) * scale_w
            box_y = bay_y + (pos_z - item_height/2) * scale_h
            box_w = item_width * scale_w
            box_h = item_height * scale_h
            
            # Draw box
            color = box_colors[item['id'] % len(box_colors)]
            c.setFillColor(color)
            c.setStrokeColor(colors.black)
            c.setLineWidth(1.5)
            c.rect(box_x, box_y, box_w, box_h, fill=1, stroke=1)
            
            # Draw label
            c.setFillColor(colors.white)
            c.setFont("Helvetica-Bold", 8)
            label = f"ID{item['id']}"
            c.drawCentredString(box_x + box_w/2, box_y + box_h/2 + 8, label)
            
            c.setFont("Helvetica", 7)
            weight = f"{item['weight']}kg"
            c.drawCentredString(box_x + box_w/2, box_y + box_h/2 - 2, weight)
            
            item_name = item['item_type']
            if len(item_name) > 15:
                item_name = item_name[:12] + "..."
            c.drawCentredString(box_x + box_w/2, box_y + box_h/2 - 12, item_name)
        
        # Draw legend
        legend_y = 150
        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, legend_y, "Items in This Slice:")
        
        c.setFont("Helvetica", 9)
        legend_y -= 20
        
        for idx, item in enumerate(items_in_slice):
            if legend_y < 50:  # Don't overflow page
                c.drawString(50, legend_y, "...and more")
                break
                
            color = box_colors[item['id'] % len(box_colors)]
            c.setFillColor(color)
            c.rect(50, legend_y - 8, 12, 12, fill=1, stroke=1)
            
            c.setFillColor(colors.black)
            text = f"ID{item['id']}: {item['item_type']} - {item['weight']}kg - Priority {item['priority']}"
            c.drawString(70, legend_y - 4, text)
            legend_y -= 18
        
        # Add page number
        c.setFont("Helvetica", 10)
        c.drawString(width - 100, 30, f"Page {quarter + 1} of 4")
        
        c.showPage()
    
    c.save()
    buffer.seek(0)
    return buffer

@app.route('/api/export-openscad', methods=['POST'])
def export_openscad():
    data = request.json
    packed = data.get('packed', [])
    max_length = float(data.get('max_length', 10))
    max_width = float(data.get('max_width', 3))
    max_height = float(data.get('max_height', 2.5))
    stats = data.get('stats', {})
    
    # Generate OpenSCAD code
    scad_code = generate_openscad(packed, max_length, max_width, max_height, stats)
    
    # Create file in memory
    output = io.BytesIO()
    output.write(scad_code.encode('utf-8'))
    output.seek(0)
    
    return send_file(
        output,
        mimetype='text/plain',
        as_attachment=True,
        download_name='cargo_manifest.scad'
    )

def generate_openscad(packed, max_length, max_width, max_height, stats):
    """Generate OpenSCAD code with semi-cylindrical cargo bay"""
    
    # Convert meters to mm for better OpenSCAD visualization
    scale = 1000
    
    scad = """// Military Cargo Loading Manifest
// Generated by Space Optimizer

"""
    
    # Add statistics as comments
    scad += f"""// === CARGO STATISTICS ===
// Total Weight: {stats.get('total_weight', 0):.1f} kg / {stats.get('max_weight', 0):.0f} kg
// Weight Utilization: {stats.get('weight_utilization', 0):.2f}%
// Volume Utilization: {stats.get('volume_utilization', 0):.2f}%
// Items Packed: {stats.get('items_packed', 0)}
// Items Unpacked: {stats.get('items_unpacked', 0)}

"""
    
    # OpenSCAD parameters
    scad += f"""// === CARGO BAY DIMENSIONS (mm) ===
bay_length = {max_length * scale};
bay_width = {max_width * scale};
bay_height = {max_height * scale};
wall_thickness = 20;

// Text settings
text_size = 50;
text_depth = 2;

$fn = 50; // Smooth curves

"""
    
    # Module for semi-cylindrical cargo bay
    scad += """// === SEMI-CYLINDRICAL CARGO BAY ===
module cargo_bay() {
    color([0.3, 0.3, 0.3, 0.3]) {
        difference() {
            // Outer semi-cylinder
            translate([bay_length/2, bay_width/2, 0])
                rotate([0, 90, 0])
                    intersection() {
                        cylinder(h=bay_length, r=bay_width/2, center=true);
                        translate([0, 0, 0])
                            cube([bay_width, bay_width, bay_length + 10], center=true);
                    }
            
            // Inner hollow
            translate([bay_length/2, bay_width/2, wall_thickness])
                rotate([0, 90, 0])
                    intersection() {
                        cylinder(h=bay_length + 10, r=bay_width/2 - wall_thickness, center=true);
                        translate([0, 0, 0])
                            cube([bay_width, bay_width, bay_length + 20], center=true);
                    }
            
            // Front opening
            translate([-5, bay_width/2, bay_height/2])
                cube([20, bay_width + 10, bay_height + 10], center=true);
        }
        
        // Floor
        translate([bay_length/2, bay_width/2, -wall_thickness/2])
            cube([bay_length, bay_width, wall_thickness], center=true);
    }
}

"""
    
    # Module for cargo box with label
    scad += """// === CARGO BOX MODULE ===
module cargo_box(x, y, z, l, w, h, color_vec, label_text, weight_text) {
    translate([x, y, z]) {
        // Box
        color(color_vec)
            cube([l, w, h], center=true);
        
        // Box edges
        color([0, 0, 0])
            translate([0, 0, 0]) {
                // Edge wireframe
                edge_r = 2;
                
                // Bottom edges
                translate([0, 0, -h/2]) {
                    translate([l/2, 0, 0]) rotate([0, 90, 0]) cylinder(h=edge_r, r=edge_r, center=true);
                    translate([-l/2, 0, 0]) rotate([0, 90, 0]) cylinder(h=edge_r, r=edge_r, center=true);
                    translate([0, w/2, 0]) rotate([90, 0, 0]) cylinder(h=edge_r, r=edge_r, center=true);
                    translate([0, -w/2, 0]) rotate([90, 0, 0]) cylinder(h=edge_r, r=edge_r, center=true);
                }
                
                // Top edges
                translate([0, 0, h/2]) {
                    translate([l/2, 0, 0]) rotate([0, 90, 0]) cylinder(h=edge_r, r=edge_r, center=true);
                    translate([-l/2, 0, 0]) rotate([0, 90, 0]) cylinder(h=edge_r, r=edge_r, center=true);
                    translate([0, w/2, 0]) rotate([90, 0, 0]) cylinder(h=edge_r, r=edge_r, center=true);
                    translate([0, -w/2, 0]) rotate([90, 0, 0]) cylinder(h=edge_r, r=edge_r, center=true);
                }
            }
        
        // Label on top
        color([1, 1, 1])
            translate([0, 0, h/2 + text_depth/2])
                linear_extrude(height=text_depth)
                    text(label_text, size=text_size, halign="center", valign="center", font="Liberation Sans:style=Bold");
        
        // Weight label on side
        color([1, 1, 0])
            translate([0, -w/2 - text_depth/2, 0])
                rotate([90, 0, 0])
                    linear_extrude(height=text_depth)
                        text(weight_text, size=text_size * 0.7, halign="center", valign="center", font="Liberation Sans:style=Bold");
    }
}

"""
    
    # Main assembly
    scad += """// === MAIN ASSEMBLY ===
cargo_bay();

"""
    
    # Add each packed item
    colors_list = [
        "[1, 0, 0, 0.8]",     # Red
        "[0, 1, 0, 0.8]",     # Green
        "[0, 0, 1, 0.8]",     # Blue
        "[1, 1, 0, 0.8]",     # Yellow
        "[1, 0, 1, 0.8]",     # Magenta
        "[0, 1, 1, 0.8]",     # Cyan
        "[1, 0.5, 0, 0.8]",   # Orange
        "[0.5, 0, 1, 0.8]"    # Purple
    ]
    
    for idx, item in enumerate(packed):
        color = colors_list[idx % len(colors_list)]
        
        # Convert position and dimensions to mm
        x = item['position']['x'] * scale
        y = item['position']['y'] * scale
        z = item['position']['z'] * scale
        l = item['length'] * scale
        w = item['width'] * scale
        h = item['height'] * scale
        
        # Create label
        label = f"ID{item['id']}"
        weight_label = f"{item['weight']}kg"
        
        scad += f"""// Item {item['id']}: {item['item_type']} (Priority: {item['priority']})
cargo_box({x}, {y}, {z}, {l}, {w}, {h}, {color}, "{label}", "{weight_label}");

"""
    
    # Add legend/info panel
    scad += f"""
// === INFO PANEL ===
color([0.2, 0.2, 0.2, 0.9])
    translate([bay_length + 500, bay_width/2, bay_height/2])
        cube([800, bay_width * 1.5, bay_height * 1.2], center=true);

color([1, 1, 1])
    translate([bay_length + 500, bay_width/2, bay_height/2 + 300])
        linear_extrude(height=5)
            text("CARGO MANIFEST", size=80, halign="center", valign="center", font="Liberation Sans:style=Bold");

color([0.8, 0.8, 0.8]) {{
    translate([bay_length + 500, bay_width/2, bay_height/2 + 150])
        linear_extrude(height=5)
            text("Weight: {stats.get('total_weight', 0):.0f}/{stats.get('max_weight', 0):.0f} kg", size=50, halign="center", valign="center");
    
    translate([bay_length + 500, bay_width/2, bay_height/2 + 50])
        linear_extrude(height=5)
            text("Util: {stats.get('weight_utilization', 0):.1f}%", size=50, halign="center", valign="center");
    
    translate([bay_length + 500, bay_width/2, bay_height/2 - 50])
        linear_extrude(height=5)
            text("Packed: {stats.get('items_packed', 0)}", size=50, halign="center", valign="center");
    
    translate([bay_length + 500, bay_width/2, bay_height/2 - 150])
        linear_extrude(height=5)
            text("Unpacked: {stats.get('items_unpacked', 0)}", size=50, halign="center", valign="center");
}}
"""
    
    return scad

@app.route('/api/item-presets', methods=['GET'])
def get_item_presets():
    return jsonify(ITEM_PRESETS)

@app.route('/api/aircraft-presets', methods=['GET'])
def get_aircraft_presets():
    return jsonify(AIRCRAFT_PRESETS)

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AirStack Space Optimizer</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 text-gray-800 min-h-screen">
    <div class="container mx-auto px-4 py-8">
        <header class="mb-8 flex items-center gap-6">
            <div class="flex items-center">
                <span class="text-6xl font-bold" style="color: #72A7C0;">Air</span>
                <span class="text-6xl font-bold" style="color: #5B6466;">Stack</span>
            </div>
            <div class="border-l-2 border-gray-300 pl-6">
                <h1 class="text-3xl font-bold" style="color: #5B6466;">Space Optimizer</h1>
            </div>
        </header>

        <div class="mb-6 flex gap-4">
            <button onclick="switchView('admin')" id="adminViewBtn" class="px-6 py-3 rounded-lg font-semibold transition text-white" style="background-color: #72A7C0;">
                Admin View
            </button>
            <button onclick="switchView('loadingcrew')" id="loadingCrewViewBtn" class="px-6 py-3 rounded-lg font-semibold transition" style="background-color: #E5E5E5; color: #5B6466;">
                Loading Crew View
            </button>
        </div>

        <div id="adminView" class="space-y-6">
            <div class="bg-white rounded-lg p-6 shadow-lg border border-gray-200">
                <h2 class="text-2xl font-bold mb-4" style="color: #72A7C0;">Submit Cargo Request</h2>
                <form id="cargoForm" class="grid grid-cols-1 md:grid-cols-4 gap-4">
                    <div>
                        <label class="block text-sm font-medium mb-2 text-gray-700">Item Type</label>
                        <select id="itemType" class="w-full bg-white border-2 border-gray-300 rounded px-3 py-2 focus:outline-none focus:border-blue-400">
                            <option value="">Select Item...</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2 text-gray-700">Quantity</label>
                        <input type="number" id="quantity" min="1" value="1" class="w-full bg-white border-2 border-gray-300 rounded px-3 py-2 focus:outline-none focus:border-blue-400">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2 text-gray-700">Priority (1-10)</label>
                        <input type="number" id="priority" min="1" max="10" value="5" class="w-full bg-white border-2 border-gray-300 rounded px-3 py-2 focus:outline-none focus:border-blue-400">
                    </div>
                    <div class="flex items-end">
                        <button type="submit" class="w-full text-white font-bold py-2 px-4 rounded transition" style="background-color: #5B6466;">
                            Add Request
                        </button>
                    </div>
                </form>
            </div>

            <div class="bg-white rounded-lg p-6 shadow-lg border border-gray-200">
                <h2 class="text-2xl font-bold mb-4" style="color: #72A7C0;">Pending Requests</h2>
                <div class="overflow-x-auto">
                    <table class="w-full text-left">
                        <thead style="background-color: #F2F2F0;">
                            <tr>
                                <th class="px-4 py-2" style="color: #5B6466;">ID</th>
                                <th class="px-4 py-2" style="color: #5B6466;">Item Type</th>
                                <th class="px-4 py-2" style="color: #5B6466;">Priority</th>
                                <th class="px-4 py-2" style="color: #5B6466;">Weight (kg)</th>
                                <th class="px-4 py-2" style="color: #5B6466;">Dimensions (m)</th>
                            </tr>
                        </thead>
                        <tbody id="requestsTable" class="divide-y divide-gray-200">
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Aircraft Configuration Section -->
            <div class="bg-white rounded-lg p-6 shadow-lg border border-gray-200">
                <h2 class="text-2xl font-bold mb-4" style="color: #72A7C0;">Aircraft Configuration</h2>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
                    <div class="md:col-span-3">
                        <label class="block text-sm font-medium mb-2 text-gray-700">Aircraft Type</label>
                        <input type="text" value="UH-60 Black Hawk" readonly class="w-full bg-gray-100 border-2 border-gray-300 rounded px-3 py-2 cursor-not-allowed text-gray-700 font-semibold">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2 text-gray-700">Max Weight (kg)</label>
                        <input type="text" value="1200" readonly class="w-full bg-gray-100 border-2 border-gray-300 rounded px-3 py-2 cursor-not-allowed text-gray-700">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2 text-gray-700">Max Length (m)</label>
                        <input type="text" value="3.8" readonly class="w-full bg-gray-100 border-2 border-gray-300 rounded px-3 py-2 cursor-not-allowed text-gray-700">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2 text-gray-700">Max Width (m)</label>
                        <input type="text" value="2.2" readonly class="w-full bg-gray-100 border-2 border-gray-300 rounded px-3 py-2 cursor-not-allowed text-gray-700">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2 text-gray-700">Max Height (m)</label>
                        <input type="text" value="1.3" readonly class="w-full bg-gray-100 border-2 border-gray-300 rounded px-3 py-2 cursor-not-allowed text-gray-700">
                    </div>
                </div>
                <button onclick="generateManifest()" class="w-full text-white font-bold py-4 px-6 rounded-lg text-xl transition" style="background-color: #72A7C0;">
                    Generate Layout
                </button>
            </div>

            <div id="resultsSection" class="space-y-6 hidden">
                <div class="bg-white rounded-lg p-6 shadow-lg border border-gray-200">
                    <h2 class="text-2xl font-bold mb-4" style="color: #5B6466;">✓ Export Ready</h2>
                    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
                        <div class="rounded p-4" style="background-color: #F2F2F0;">
                            <div class="text-sm" style="color: #5B6466;">Weight Used</div>
                            <div class="text-xl font-bold" id="weightUsed" style="color: #5B6466;">0 kg</div>
                        </div>
                        <div class="rounded p-4" style="background-color: #F2F2F0;">
                            <div class="text-sm" style="color: #5B6466;">Weight Utilization</div>
                            <div class="text-xl font-bold" id="weightUtil" style="color: #5B6466;">0%</div>
                        </div>
                        <div class="rounded p-4" style="background-color: #F2F2F0;">
                            <div class="text-sm" style="color: #5B6466;">Items Packed</div>
                            <div class="text-xl font-bold" id="packedCount" style="color: #72A7C0;">0</div>
                        </div>
                        <div class="rounded p-4" style="background-color: #F2F2F0;">
                            <div class="text-sm" style="color: #5B6466;">Items Unpacked</div>
                            <div class="text-xl font-bold text-red-600" id="unpackedCount">0</div>
                        </div>
                    </div>
                    
                    <div class="bg-blue-50 border-2 border-blue-200 rounded-lg p-4 mb-6">
                        <h3 class="font-bold text-lg mb-3" style="color: #5B6466;">⚖️ Weight Balance</h3>
                        <div class="grid grid-cols-2 md:grid-cols-3 gap-4">
                            <div>
                                <div class="text-sm text-gray-600">Balance Score</div>
                                <div class="text-2xl font-bold" id="balanceScore" style="color: #72A7C0;">100%</div>
                            </div>
                            <div>
                                <div class="text-sm text-gray-600">Front Weight</div>
                                <div class="text-xl font-bold" id="leftWeight" style="color: #5B6466;">0 kg</div>
                            </div>
                            <div>
                                <div class="text-sm text-gray-600">Rear Weight</div>
                                <div class="text-xl font-bold" id="rightWeight" style="color: #5B6466;">0 kg</div>
                            </div>
                        </div>
                        <div class="mt-3 text-sm text-gray-600">
                            Center of Gravity: <span id="cogDisplay" class="font-mono">-</span>
                        </div>
                    </div>
                    
                    <div class="space-y-3">
                        <button onclick="downloadPDF()" class="w-full text-white font-bold py-3 px-6 rounded-lg transition flex items-center justify-center gap-2" style="background-color: #72A7C0;">
                            <span>📄</span> Download Loading Plan PDF (Ground Crew)
                        </button>
                        <button onclick="downloadOpenSCAD()" class="w-full text-white font-bold py-3 px-6 rounded-lg transition flex items-center justify-center gap-2" style="background-color: #5B6466;">
                            <span>⬇</span> Download OpenSCAD File (.scad)
                        </button>
                    </div>
                    
                    <p class="text-sm text-gray-600 mt-4">
                        Download the ground crew loading plan (PDF with 4 vertical slices) or the 3D model (OpenSCAD).
                    </p>
                </div>
            </div>

            <div class="flex gap-4">
                <button onclick="clearAllRequests()" class="flex-1 bg-red-600 hover:bg-red-700 text-white font-bold py-3 px-6 rounded-lg transition">
                    Clear All Requests
                </button>
            </div>
        </div>

        <div id="loadingCrewView" class="space-y-6 hidden">
            <div class="bg-white rounded-lg p-6 shadow-lg border border-gray-200">
                <div class="flex justify-between items-center mb-4">
                    <h2 class="text-2xl font-bold" style="color: #72A7C0;">Current Loading Plan</h2>
                    <button onclick="refreshLoadingCrewView()" class="px-4 py-2 text-white rounded-lg transition" style="background-color: #72A7C0;">
                        🔄 Refresh
                    </button>
                </div>
                
                <div id="noPlanMessage" class="text-center py-12 text-gray-500">
                    <p class="text-xl mb-2">⏳ No loading plan available yet</p>
                    <p class="text-sm">Waiting for admin to generate a layout...</p>
                </div>
                
                <div id="planContent" class="hidden">
                    <!-- PDF Viewer - 4 slice images displayed here -->
                    <div id="pdfSlices" class="space-y-6">
                        <!-- Slices will be rendered as canvases here -->
                    </div>
                    
                    <div class="mt-6">
                        <button onclick="downloadLoadingCrewPDF()" class="w-full text-white font-bold py-4 px-6 rounded-lg text-xl transition flex items-center justify-center gap-2" style="background-color: #72A7C0;">
                            <span>📄</span> Download Complete Loading Plan PDF
                        </button>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let itemPresets = {};
        let aircraftPresets = {};
        let lastOptimizationResult = null;
        let lastAircraftConfig = null;

        async function init() {
            await loadItemPresets();
            await loadAircraftPresets();
            await loadRequests();
        }

        async function loadItemPresets() {
            const response = await fetch('/api/item-presets');
            itemPresets = await response.json();
            
            const select = document.getElementById('itemType');
            Object.keys(itemPresets).forEach(item => {
                const option = document.createElement('option');
                option.value = item;
                option.textContent = item;
                select.appendChild(option);
            });
        }

        async function loadAircraftPresets() {
            const response = await fetch('/api/aircraft-presets');
            aircraftPresets = await response.json();
        }

        function switchView(view) {
            if (view === 'admin') {
                document.getElementById('adminView').classList.remove('hidden');
                document.getElementById('loadingCrewView').classList.add('hidden');
                document.getElementById('adminViewBtn').style.backgroundColor = '#72A7C0';
                document.getElementById('adminViewBtn').style.color = 'white';
                document.getElementById('loadingCrewViewBtn').style.backgroundColor = '#E5E5E5';
                document.getElementById('loadingCrewViewBtn').style.color = '#5B6466';
                loadRequests();
            } else if (view === 'loadingcrew') {
                document.getElementById('adminView').classList.add('hidden');
                document.getElementById('loadingCrewView').classList.remove('hidden');
                document.getElementById('loadingCrewViewBtn').style.backgroundColor = '#72A7C0';
                document.getElementById('loadingCrewViewBtn').style.color = 'white';
                document.getElementById('adminViewBtn').style.backgroundColor = '#E5E5E5';
                document.getElementById('adminViewBtn').style.color = '#5B6466';
                loadLoadingCrewPlan();
            }
        }

        document.getElementById('cargoForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const itemType = document.getElementById('itemType').value;
            const quantity = document.getElementById('quantity').value;
            const priority = document.getElementById('priority').value;
            
            if (!itemType) {
                alert('Please select an item type');
                return;
            }
            
            const response = await fetch('/api/requests', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ item_type: itemType, quantity, priority })
            });
            
            const result = await response.json();
            if (result.success) {
                alert(result.message);
                document.getElementById('cargoForm').reset();
                document.getElementById('quantity').value = 1;
                document.getElementById('priority').value = 5;
                await loadRequests();
            }
        });

        async function loadRequests() {
            const response = await fetch('/api/requests');
            const requests = await response.json();
            
            const tbody = document.getElementById('requestsTable');
            tbody.innerHTML = '';
            
            requests.forEach(req => {
                const row = document.createElement('tr');
                row.className = 'hover:bg-gray-50';
                row.innerHTML = `
                    <td class="px-4 py-2">${req.id}</td>
                    <td class="px-4 py-2">${req.item_type}</td>
                    <td class="px-4 py-2">
                        <span class="px-2 py-1 rounded text-xs font-bold ${getPriorityColor(req.priority)}">
                            ${req.priority}
                        </span>
                    </td>
                    <td class="px-4 py-2">${req.weight}</td>
                    <td class="px-4 py-2">${req.length} × ${req.width} × ${req.height}</td>
                `;
                tbody.appendChild(row);
            });
        }

        function getPriorityColor(priority) {
            if (priority >= 8) return 'bg-red-600 text-white';
            if (priority >= 5) return 'bg-yellow-600 text-white';
            return 'bg-green-600 text-white';
        }

        async function generateManifest() {
            // Use locked UH-60 Black Hawk specs
            const maxWeight = 1200;
            const maxLength = 3.8;
            const maxWidth = 2.2;
            const maxHeight = 1.3;
            
            const response = await fetch('/api/optimize', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    max_weight: maxWeight,
                    max_length: maxLength,
                    max_width: maxWidth,
                    max_height: maxHeight
                })
            });
            
            const result = await response.json();
            lastOptimizationResult = result;
            lastAircraftConfig = {
                max_weight: maxWeight,
                max_length: maxLength,
                max_width: maxWidth,
                max_height: maxHeight
            };
            
            displayResults(result);
        }

        function displayResults(result) {
            document.getElementById('resultsSection').classList.remove('hidden');
            
            document.getElementById('weightUsed').textContent = `${result.stats.total_weight.toFixed(1)} kg`;
            document.getElementById('weightUtil').textContent = `${result.stats.weight_utilization}%`;
            document.getElementById('packedCount').textContent = result.stats.items_packed;
            document.getElementById('unpackedCount').textContent = result.stats.items_unpacked;
            
            // Display balance information
            document.getElementById('balanceScore').textContent = `${result.stats.balance_score}%`;
            document.getElementById('leftWeight').textContent = `${result.stats.left_weight} kg`;
            document.getElementById('rightWeight').textContent = `${result.stats.right_weight} kg`;
            
            const cog = result.stats.center_of_gravity;
            document.getElementById('cogDisplay').textContent = `X:${cog.x}m Y:${cog.y}m Z:${cog.z}m`;
        }

        async function downloadOpenSCAD() {
            if (!lastOptimizationResult || !lastAircraftConfig) {
                alert('Please generate a manifest first');
                return;
            }
            
            const response = await fetch('/api/export-openscad', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    packed: lastOptimizationResult.packed,
                    max_length: lastAircraftConfig.max_length,
                    max_width: lastAircraftConfig.max_width,
                    max_height: lastAircraftConfig.max_height,
                    stats: lastOptimizationResult.stats
                })
            });
            
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'cargo_manifest.scad';
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
        }

        async function downloadPDF() {
            if (!lastOptimizationResult || !lastAircraftConfig) {
                alert('Please generate a manifest first');
                return;
            }
            
            const response = await fetch('/api/export-pdf', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    packed: lastOptimizationResult.packed,
                    max_length: lastAircraftConfig.max_length,
                    max_width: lastAircraftConfig.max_width,
                    max_height: lastAircraftConfig.max_height,
                    stats: lastOptimizationResult.stats
                })
            });
            
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'loading_plan.pdf';
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
        }

        async function clearAllRequests() {
            if (!confirm('Are you sure you want to clear all cargo requests?')) {
                return;
            }
            
            const response = await fetch('/api/requests/clear', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            
            const result = await response.json();
            if (result.success) {
                alert(result.message);
                await loadRequests();
                document.getElementById('resultsSection').classList.add('hidden');
                lastOptimizationResult = null;
                lastAircraftConfig = null;
            }
        }

        // Loading Crew View Functions
        async function loadLoadingCrewPlan() {
            try {
                const response = await fetch('/api/latest-plan');
                
                if (response.ok) {
                    const plan = await response.json();
                    displayLoadingCrewPlan(plan);
                } else {
                    showNoPlanMessage();
                }
            } catch (error) {
                console.error('Error loading plan:', error);
                showNoPlanMessage();
            }
        }

        function showNoPlanMessage() {
            document.getElementById('noPlanMessage').classList.remove('hidden');
            document.getElementById('planContent').classList.add('hidden');
        }

        function displayLoadingCrewPlan(plan) {
            document.getElementById('noPlanMessage').classList.add('hidden');
            document.getElementById('planContent').classList.remove('hidden');
            
            // Render the 4 PDF slices visually
            renderPDFSlices(plan);
            
            // Store plan for PDF download
            lastOptimizationResult = plan;
            lastAircraftConfig = {
                max_weight: plan.stats.max_weight,
                max_length: plan.aircraft.max_length,
                max_width: plan.aircraft.max_width,
                max_height: plan.aircraft.max_height
            };
        }

        function renderPDFSlices(plan) {
            const container = document.getElementById('pdfSlices');
            container.innerHTML = '';
            
            const maxLength = plan.aircraft.max_length;
            const maxWidth = plan.aircraft.max_width;
            const maxHeight = plan.aircraft.max_height;
            const quarterWidth = maxLength / 4;
            
            // Create 4 slices
            for (let quarter = 0; quarter < 4; quarter++) {
                const sliceStart = quarter * quarterWidth;
                const sliceEnd = (quarter + 1) * quarterWidth;
                
                // Filter items in this slice
                const itemsInSlice = plan.packed.filter(item => {
                    const itemX = item.position.x;
                    const itemLength = item.length;
                    const itemStart = itemX - itemLength/2;
                    const itemEnd = itemX + itemLength/2;
                    return itemStart < sliceEnd && itemEnd > sliceStart;
                });
                
                // Create slice container
                const sliceDiv = document.createElement('div');
                sliceDiv.className = 'bg-white border-2 border-gray-300 rounded-lg p-4';
                
                const title = document.createElement('h3');
                title.className = 'font-bold text-lg mb-3';
                title.style.color = '#5B6466';
                title.textContent = `Slice ${quarter + 1} of 4 (${sliceStart.toFixed(1)}m - ${sliceEnd.toFixed(1)}m)`;
                sliceDiv.appendChild(title);
                
                // Create canvas for visualization
                const canvas = document.createElement('canvas');
                canvas.width = 800;
                canvas.height = 600;
                canvas.className = 'w-full border border-gray-200 rounded';
                sliceDiv.appendChild(canvas);
                
                // Draw the slice
                drawSlice(canvas, itemsInSlice, maxWidth, maxHeight, plan.stats);
                
                container.appendChild(sliceDiv);
            }
        }

        function drawSlice(canvas, items, maxWidth, maxHeight, stats) {
            const ctx = canvas.getContext('2d');
            const padding = 50;
            const drawWidth = canvas.width - 2 * padding;
            const drawHeight = canvas.height - 2 * padding;
            
            // Clear canvas
            ctx.fillStyle = '#F9FAFB';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            
            // Draw title info
            ctx.fillStyle = '#5B6466';
            ctx.font = 'bold 16px Arial';
            ctx.fillText(`UH-60 Black Hawk - Top View`, padding, 30);
            
            ctx.font = '12px Arial';
            ctx.fillStyle = '#6B7280';
            ctx.fillText(`Weight: ${stats.total_weight.toFixed(1)}/${stats.max_weight} kg | Balance: ${stats.balance_score}%`, padding, canvas.height - 20);
            
            // Scale factors
            const scaleW = drawWidth / maxWidth;
            const scaleH = drawHeight / maxHeight;
            
            // Draw cargo bay outline
            ctx.strokeStyle = '#1F2937';
            ctx.lineWidth = 3;
            ctx.strokeRect(padding, padding, drawWidth, drawHeight);
            
            // Draw grid
            ctx.strokeStyle = '#D1D5DB';
            ctx.lineWidth = 1;
            for (let i = 0; i <= maxWidth; i += 0.5) {
                const x = padding + i * scaleW;
                ctx.beginPath();
                ctx.moveTo(x, padding);
                ctx.lineTo(x, padding + drawHeight);
                ctx.stroke();
            }
            for (let i = 0; i <= maxHeight; i += 0.5) {
                const y = padding + i * scaleH;
                ctx.beginPath();
                ctx.moveTo(padding, y);
                ctx.lineTo(padding + drawWidth, y);
                ctx.stroke();
            }
            
            // Draw items
            const colors = [
                '#EF4444', '#10B981', '#3B82F6', '#F59E0B',
                '#EC4899', '#06B6D4', '#F97316', '#8B5CF6'
            ];
            
            items.forEach((item, idx) => {
                const posY = item.position.y;
                const posZ = item.position.z;
                const itemWidth = item.width;
                const itemHeight = item.height;
                
                const x = padding + (posY - itemWidth/2) * scaleW;
                // Flip Z axis - subtract from max to invert
                const y = padding + drawHeight - ((posZ + itemHeight/2) * scaleH);
                const w = itemWidth * scaleW;
                const h = itemHeight * scaleH;
                
                // Draw box
                ctx.fillStyle = colors[item.id % colors.length];
                ctx.fillRect(x, y, w, h);
                
                ctx.strokeStyle = '#000';
                ctx.lineWidth = 2;
                ctx.strokeRect(x, y, w, h);
                
                // Draw label
                ctx.fillStyle = '#FFF';
                ctx.font = 'bold 12px Arial';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(`ID${item.id}`, x + w/2, y + h/2 - 8);
                
                ctx.font = '10px Arial';
                ctx.fillText(`${item.weight}kg`, x + w/2, y + h/2 + 6);
            });
            
            ctx.textAlign = 'left';
        }

        function refreshLoadingCrewView() {
            loadLoadingCrewPlan();
        }

        async function downloadLoadingCrewPDF() {
            if (!lastOptimizationResult || !lastAircraftConfig) {
                alert('No load plan available to download');
                return;
            }
            
            const response = await fetch('/api/export-pdf', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    packed: lastOptimizationResult.packed,
                    max_length: lastAircraftConfig.max_length,
                    max_width: lastAircraftConfig.max_width,
                    max_height: lastAircraftConfig.max_height,
                    stats: lastOptimizationResult.stats
                })
            });
            
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'loading_plan.pdf';
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
        }

        init();
    </script>
</body>
</html>
'''

if __name__ == '__main__':
    app.run(debug=True, port=5000)
