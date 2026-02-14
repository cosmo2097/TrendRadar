import sys
import xml.etree.ElementTree as ET
import re

def parse_opml(file_path):
    tree = ET.parse(file_path)
    root = tree.getroot()
    feeds = []
    
    # OPML structure: opml -> body -> outline -> outline (usually recursed)
    for outline in root.findall(".//outline[@type='rss']"):
        xml_url = outline.get('xmlUrl')
        text = outline.get('text')
        if xml_url and text:
            feeds.append({
                'name': text,
                'url': xml_url
            })
    return feeds

def generate_yaml(feeds):
    yaml_lines = []
    
    for feed in feeds:
        feed_id = ""
        # Try to extract ID from Wechat2RSS URL pattern: /feed/123456.xml
        match = re.search(r'/feed/(\d+)\.xml', feed['url'])
        if match:
            feed_id = f"wx-{match.group(1)}"
        else:
            # Fallback: simple hash or slug
            # Simple hash of URL to ensure uniqueness but consistency
            import hashlib
            hash_object = hashlib.md5(feed['url'].encode())
            feed_id = f"feed-{hash_object.hexdigest()[:8]}"
            
        yaml_lines.append(f'    - id: "{feed_id}"')
        yaml_lines.append(f'      name: "{feed["name"]}"')
        yaml_lines.append(f'      url: "{feed["url"]}"')
        yaml_lines.append('') # Empty line for spacing
        
    return "\n".join(yaml_lines)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python opml_to_config.py <path_to_opml_file>")
        sys.exit(1)
        
    opml_file = sys.argv[1]
    feeds = parse_opml(opml_file)
    yaml_output = generate_yaml(feeds)
    
    # Print the output to stdout so the user can copy it or redirect it
    print(yaml_output)
