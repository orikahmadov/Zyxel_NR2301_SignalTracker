import time
import json
import logging
import re
import sys
import os
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import colorama
from colorama import Fore, Style

# Audio imports
try:
    import winsound  # Windows built-in audio
    WINSOUND_AVAILABLE = True
except ImportError:
    WINSOUND_AVAILABLE = False

try:
    import pygame
    pygame.mixer.init()
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False

# Initialize colorama for cross-platform colored output
colorama.init()

class RouterSignalTracker:
    """
    A class to monitor and display live signal data from Zyxel NR2301 5G portable router.
    """
    
    def __init__(self, router_url="http://192.168.1.1", username="", password="", debug_mode=False, audio_feedback=True):
        """
        Initialize the router signal tracker.
        
        Args:
            router_url (str): Router's IP address or URL
            username (str): Router login username
            password (str): Router login password
            debug_mode (bool): Enable debug mode for troubleshooting
            audio_feedback (bool): Enable audio feedback for signal quality
        """
        self.router_url = router_url
        self.username = username
        self.password = password
        self.debug_mode = debug_mode
        self.audio_feedback = audio_feedback
        self.driver = None
        self.wait = None
        
        # Get credentials if not provided
        if not self.username or not self.password:
            self.get_credentials()
        
        # Audio file paths
        self.audio_files = {
            'excellent': 'sound/excellent_signal.wav',
            'good': 'sound/good_signal.wav',
            'bad': 'sound/bad_signal.wav'
        }
        
        # Signal quality thresholds
        self.signal_thresholds = {
            'rssi': {'excellent': -50, 'good': -70, 'fair': -85, 'poor': -100},
            'rsrp': {'excellent': -80, 'good': -90, 'fair': -100, 'poor': -110},
            'rsrq': {'excellent': -10, 'good': -15, 'fair': -20, 'poor': -25},
            'sinr': {'excellent': 20, 'good': 13, 'fair': 0, 'poor': -3}
        }
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('router_signal.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    def get_credentials(self):
        """Get router credentials from user input."""
        print(f"{Fore.CYAN}Router Authentication Required{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}Please enter your router credentials:{Style.RESET_ALL}")
        
        if not self.username:
            self.username = input(f"{Fore.CYAN}Username (default: admin): {Style.RESET_ALL}").strip()
            if not self.username:
                self.username = "admin"
        
        if not self.password:
            import getpass
            self.password = getpass.getpass(f"{Fore.CYAN}Password: {Style.RESET_ALL}")
            if not self.password:
                print(f"{Fore.RED}Password is required!{Style.RESET_ALL}")
                sys.exit(1)
        
        print(f"{Fore.GREEN}Credentials configured successfully!{Style.RESET_ALL}")
    
    def play_audio(self, audio_type):
        """
        Play audio feedback based on signal quality.
        
        Args:
            audio_type (str): Type of audio to play ('excellent', 'good', 'bad')
        """
        if not self.audio_feedback:
            return
            
        audio_file = self.audio_files.get(audio_type)
        if not audio_file or not os.path.exists(audio_file):
            self.logger.warning("Audio file not found: %s", audio_file)
            return
        
        try:
            if WINSOUND_AVAILABLE:
                # Use Windows built-in winsound for WAV files
                winsound.PlaySound(audio_file, winsound.SND_FILENAME | winsound.SND_ASYNC)
                self.logger.info("Playing audio: %s", audio_type)
            elif PYGAME_AVAILABLE:
                # Use pygame as fallback
                sound = pygame.mixer.Sound(audio_file)
                sound.play()
                self.logger.info("Playing audio: %s", audio_type)
            else:
                self.logger.warning("No audio library available (winsound or pygame)")
        except Exception as e:
            self.logger.error("Failed to play audio %s: %s", audio_type, e)
    
    def get_overall_signal_quality(self, signal_data):
        """
        Determine overall signal quality based on multiple metrics.
        
        Args:
            signal_data (dict): Signal data dictionary
            
        Returns:
            str: Overall quality ('excellent', 'good', 'bad')
        """
        if not signal_data:
            return 'bad'
        
        # Extract numeric values for key metrics
        metrics = {}
        
        # PCC B7 metrics
        pcc_data = signal_data.get('pcc_b7', {})
        for field_name, value in pcc_data.items():
            if value != "N/A":
                numeric_value = self.extract_numeric_value(value)
                if numeric_value is not None:
                    if 'RSSI' in field_name:
                        metrics['rssi'] = numeric_value
                    elif 'RSRP' in field_name:
                        metrics['rsrp'] = numeric_value
                    elif 'RSRQ' in field_name:
                        metrics['rsrq'] = numeric_value
                    elif 'SINR' in field_name:
                        metrics['sinr'] = numeric_value
        
        # PSCC N78 metrics (use as additional data points)
        pscc_data = signal_data.get('pscc_n78', {})
        for field_name, value in pscc_data.items():
            if value != "N/A":
                numeric_value = self.extract_numeric_value(value)
                if numeric_value is not None:
                    if 'RSRP' in field_name and 'rsrp' not in metrics:
                        metrics['rsrp'] = numeric_value
                    elif 'RSRQ' in field_name and 'rsrq' not in metrics:
                        metrics['rsrq'] = numeric_value
                    elif 'SINR' in field_name and 'sinr' not in metrics:
                        metrics['sinr'] = numeric_value
        
        if not metrics:
            return 'bad'
        
        # Score each metric
        scores = []
        for metric, value in metrics.items():
            if metric in self.signal_thresholds:
                thresholds = self.signal_thresholds[metric]
                if value >= thresholds['excellent']:
                    scores.append(4)  # Excellent
                elif value >= thresholds['good']:
                    scores.append(3)  # Good
                elif value >= thresholds['fair']:
                    scores.append(2)  # Fair
                else:
                    scores.append(1)  # Poor
        
        if not scores:
            return 'bad'
        
        # Calculate average score
        avg_score = sum(scores) / len(scores)
        
        # Determine overall quality
        if avg_score >= 3.5:
            return 'excellent'
        elif avg_score >= 2.5:
            return 'good'
        else:
            return 'bad'
    
    def clear_screen(self):
        """Clear the terminal screen."""
        try:
            # For Windows
            if os.name == 'nt':
                os.system('cls')
            # For Unix/Linux/Mac
            else:
                os.system('clear')
        except:
            # Fallback: print newlines to clear screen
            print('\n' * 50)
    
    def setup_driver(self):
        """Setup Chrome WebDriver with appropriate options."""
        try:
            chrome_options = Options()
            
            # Only run headless if not in debug mode
            if not self.debug_mode:
                chrome_options.add_argument("--headless")  # Run in background
            
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            
            # Add additional options for better compatibility
            chrome_options.add_argument("--disable-web-security")
            chrome_options.add_argument("--allow-running-insecure-content")
            chrome_options.add_argument("--disable-extensions")
            
            self.driver = webdriver.Chrome(options=chrome_options)
            self.wait = WebDriverWait(self.driver, 10)
            
            if self.debug_mode:
                self.logger.info("Chrome WebDriver initialized in DEBUG mode (visible browser)")
            else:
                self.logger.info("Chrome WebDriver initialized successfully")
            return True
        except Exception as e:
            self.logger.error("Failed to initialize WebDriver: %s", e)
            return False
    
    def login(self):
        """Login to the router interface."""
        try:
            # Navigate to login page
            login_url = f"{self.router_url}/login.html"
            self.driver.get(login_url)
            self.logger.info("Navigating to %s", login_url)
            
            # Wait for page to load completely
            time.sleep(3)
            
            # Wait for login form to load
            username_field = self.wait.until(
                EC.presence_of_element_located((By.ID, "admin_username"))
            )
            password_field = self.driver.find_element(By.ID, "admin_password")
            login_button = self.driver.find_element(By.ID, "btn_login")
            
            self.logger.info("Login form elements found")
            
            # Enter credentials with small delays
            username_field.clear()
            time.sleep(0.5)
            username_field.send_keys(self.username)
            time.sleep(0.5)
            
            password_field.clear()
            time.sleep(0.5)
            password_field.send_keys(self.password)
            time.sleep(0.5)
            
            self.logger.info("Credentials entered, clicking login button")
            
            # Click login button
            login_button.click()
            self.logger.info("Login button clicked")
            
            # Wait a bit for the request to process
            time.sleep(2)
            
            # Wait for successful login with multiple possible indicators
            try:
                # Wait longer and check multiple possible success indicators
                WebDriverWait(self.driver, 30).until(
                    lambda driver: (
                        "html/set_net_info.html" in driver.current_url or 
                        "Network Information" in driver.page_source or
                        "app-modules" in driver.page_source or
                        "dashboard" in driver.current_url.lower() or
                        "home" in driver.current_url.lower() or
                        "index.html" in driver.current_url or  # Added for zyxel.home/index.html
                        "zyxel.home" in driver.current_url or  # Added for zyxel.home domain
                        driver.current_url != login_url  # URL changed from login page
                    )
                )
                self.logger.info("Login successful - redirected to: %s", self.driver.current_url)
                return True
            except TimeoutException:
                # Log current page info for debugging
                current_url = self.driver.current_url
                page_title = self.driver.title
                self.logger.error("Login timeout - Current URL: %s, Page Title: %s", current_url, page_title)
                
                # Debug: Save screenshot if in debug mode
                if self.debug_mode:
                    try:
                        self.driver.save_screenshot("login_debug.png")
                        self.logger.info("Debug screenshot saved as login_debug.png")
                    except Exception as e:
                        self.logger.error("Failed to save debug screenshot: %s", e)
                
                # Check if we're still on login page
                if "login" in current_url.lower():
                    self.logger.error("Still on login page - trying alternative login methods")
                    # Try alternative login methods
                    if self.try_alternative_login():
                        return True
                    self.logger.error("All login methods failed - credentials may be incorrect")
                else:
                    self.logger.error("Redirected but not to expected page")
                
                return False
                
        except Exception as e:
            self.logger.error("Login failed: %s", e)
            return False
    
    def try_alternative_login(self):
        """Try alternative login methods if standard login fails."""
        try:
            self.logger.info("Trying alternative login methods...")
            
            # Try different URLs
            alternative_urls = [
                f"{self.router_url}/login.html",
                f"{self.router_url}/",
                f"{self.router_url}/index.html",
                "http://zyxel.home/login.html",
                "http://zyxel.home/",
                "http://zyxel.home/index.html"
            ]
            
            for url in alternative_urls:
                try:
                    self.logger.info("Trying URL: %s", url)
                    self.driver.get(url)
                    time.sleep(3)
                    
                    # Check if we can find login elements
                    try:
                        username_field = self.driver.find_element(By.ID, "admin_username")
                        password_field = self.driver.find_element(By.ID, "admin_password")
                        login_button = self.driver.find_element(By.ID, "btn_login")
                        
                        # Try login
                        username_field.clear()
                        username_field.send_keys(self.username)
                        password_field.clear()
                        password_field.send_keys(self.password)
                        login_button.click()
                        
                        time.sleep(5)
                        
                        # Check if login was successful
                        if self.driver.current_url != url:
                            self.logger.info("Alternative login successful via: %s", url)
                            return True
                            
                    except NoSuchElementException:
                        self.logger.info("Login elements not found on: %s", url)
                        continue
                        
                except Exception as e:
                    self.logger.info("Failed to access %s: %s", url, e)
                    continue
            
            return False
            
        except Exception as e:
            self.logger.error("Alternative login failed: %s", e)
            return False
    
    def navigate_to_network_info(self):
        """Navigate to the Network Information page."""
        try:
            # If not already on network info page, navigate to it
            if "set_net_info.html" not in self.driver.current_url:
                # Check if we're on the homepage and need to navigate through the menu
                if "index.html" in self.driver.current_url or "zyxel.home" in self.driver.current_url:
                    self.logger.info("On homepage, navigating through APP MODULE menu")
                    
                    # Step 1: Click on the APP MODULE menu item
                    try:
                        app_module_menu = self.wait.until(
                            EC.element_to_be_clickable((By.XPATH, "//li[@class='menu-item']//a[contains(@onclick, 'html/module.html')]"))
                        )
                        app_module_menu.click()
                        self.logger.info("Clicked APP MODULE menu item")
                        
                        # Wait for the module page to load (it doesn't reload, just shows the modules)
                        time.sleep(2)
                        
                    except TimeoutException:
                        self.logger.error("Could not find APP MODULE menu item")
                        return False
                    
                    # Step 2: Look for the Network Information module
                    try:
                        network_module = self.wait.until(
                            EC.element_to_be_clickable((By.XPATH, "//div[@class='app-modules' and contains(@onclick, 'set_net_info.html')]"))
                        )
                        network_module.click()
                        self.logger.info("Clicked Network Information module")
                        
                    except TimeoutException:
                        # Try alternative selectors for the Network Information module
                        try:
                            # Look for any element containing "Network Information" text
                            network_module = self.wait.until(
                                EC.element_to_be_clickable((By.XPATH, "//*[contains(text(), 'Network Information')]"))
                            )
                            network_module.click()
                            self.logger.info("Clicked Network Information module (alternative selector)")
                        except TimeoutException:
                            self.logger.error("Could not find Network Information module after clicking APP MODULE")
                            return False
                else:
                    # Try to navigate directly to the network info page
                    network_info_url = f"{self.router_url}/html/set_net_info.html"
                    self.driver.get(network_info_url)
                    self.logger.info("Navigated directly to Network Information page")
            
            # Wait for network info content to load (check for signal data elements)
            try:
                # Wait for connection status element to appear
                self.wait.until(
                    EC.presence_of_element_located((By.ID, "connStatus"))
                )
                self.logger.info("Network Information page loaded successfully")
                return True
            except TimeoutException:
                # If connStatus not found, try other signal elements
                try:
                    self.wait.until(
                        EC.presence_of_element_located((By.ID, "sigStr"))
                    )
                    self.logger.info("Signal data elements found")
                    return True
                except TimeoutException:
                    self.logger.error("Could not find signal data elements")
                    return False
            
        except Exception as e:
            self.logger.error("Failed to navigate to Network Information: %s", e)
            return False
    
    def get_signal_quality_color(self, metric, value):
        """Get color code for signal quality."""
        if metric not in self.signal_thresholds:
            return Fore.WHITE
        
        thresholds = self.signal_thresholds[metric]
        
        if value >= thresholds['excellent']:
            return Fore.GREEN
        elif value >= thresholds['good']:
            return Fore.YELLOW
        elif value >= thresholds['fair']:
            return Fore.YELLOW
        else:
            return Fore.RED
    
    def get_signal_quality_text(self, metric, value):
        """Get text description for signal quality."""
        if metric not in self.signal_thresholds:
            return "Unknown"
        
        thresholds = self.signal_thresholds[metric]
        
        if value >= thresholds['excellent']:
            return "Excellent"
        elif value >= thresholds['good']:
            return "Good"
        elif value >= thresholds['fair']:
            return "Fair"
        else:
            return "Poor"
    
    def parse_signal_data(self):
        """Parse signal data from the Network Information page."""
        try:
            # Refresh the page to get latest data
            self.driver.refresh()
            time.sleep(2)
            
            # Wait for content to load
            self.wait.until(EC.presence_of_element_located((By.ID, "connStatus")))
            
            signal_data = {
                'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'connection_info': {},
                'pcc_b7': {},
                'pscc_n78': {}
            }
            
            # Parse connection information
            connection_fields = {
                'connStatus': 'Connection Status',
                'rat': 'RAT Mode',
                'netOp': 'Network Operator',
                'imsi': 'IMSI',
                'opBand': 'Operation Band'
            }
            
            for field_id, field_name in connection_fields.items():
                try:
                    element = self.driver.find_element(By.ID, field_id)
                    signal_data['connection_info'][field_name] = element.text.strip()
                except NoSuchElementException:
                    signal_data['connection_info'][field_name] = "N/A"
            
            # Parse PCC B7 signal data
            pcc_fields = {
                'sigStr': 'Signal Strength (RSSI)',
                'sinr': 'SINR',
                'rsrp': 'RSRP',
                'rsrq': 'RSRQ'
            }
            
            for field_id, field_name in pcc_fields.items():
                try:
                    element = self.driver.find_element(By.ID, field_id)
                    value_text = element.text.strip()
                    signal_data['pcc_b7'][field_name] = value_text
                except NoSuchElementException:
                    signal_data['pcc_b7'][field_name] = "N/A"
            
            # Parse PSCC N78 signal data
            pscc_fields = {
                'sinr1': 'SINR',
                'rsrp1': 'RSRP',
                'rsrq1': 'RSRQ'
            }
            
            for field_id, field_name in pscc_fields.items():
                try:
                    element = self.driver.find_element(By.ID, field_id)
                    value_text = element.text.strip()
                    signal_data['pscc_n78'][field_name] = value_text
                except NoSuchElementException:
                    signal_data['pscc_n78'][field_name] = "N/A"
            
            return signal_data
            
        except Exception as e:
            self.logger.error("Failed to parse signal data: %s", e)
            return None
    
    def extract_numeric_value(self, value_str):
        """Extract numeric value from signal data string."""
        if not value_str or value_str == "N/A":
            return None
        
        # Remove units and extract number
        match = re.search(r'(-?\d+(?:\.\d+)?)', value_str)
        if match:
            return float(match.group(1))
        return None
    
    def display_signal_data(self, signal_data, clear_screen=True, compact_mode=False):
        """Display signal data with color coding."""
        if not signal_data:
            print(f"{Fore.RED}Failed to retrieve signal data{Style.RESET_ALL}")
            return
        
        # Clear screen if requested
        if clear_screen:
            self.clear_screen()
        
        if compact_mode:
            # Compact display for continuous monitoring
            print(f"{Fore.CYAN}ZYXEL NR2301 5G SIGNAL MONITOR - {signal_data['timestamp']}{Style.RESET_ALL}")
            print(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
            
            # Connection info in one line
            conn_info = signal_data['connection_info']
            print(f"{Fore.YELLOW}Status: {Fore.CYAN}{conn_info.get('Connection Status', 'N/A')}{Style.RESET_ALL} | "
                  f"{Fore.YELLOW}Mode: {Fore.CYAN}{conn_info.get('RAT Mode', 'N/A')}{Style.RESET_ALL} | "
                  f"{Fore.YELLOW}Operator: {Fore.CYAN}{conn_info.get('Network Operator', 'N/A')}{Style.RESET_ALL}")
        else:
            # Full display for single readings
            print(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
            print(f"{Fore.CYAN}ZYXEL NR2301 5G ROUTER SIGNAL MONITOR{Style.RESET_ALL}")
            print(f"{Fore.CYAN}Last Updated: {signal_data['timestamp']}{Style.RESET_ALL}")
            print(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
            
            # Display connection information
            print(f"\n{Fore.YELLOW}CONNECTION INFORMATION{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}{'-'*30}{Style.RESET_ALL}")
            for key, value in signal_data['connection_info'].items():
                print(f"{Fore.WHITE}{key:20}: {Fore.CYAN}{value}{Style.RESET_ALL}")
        
        if compact_mode:
            # Compact PCC B7 display in table format
            print(f"\n{Fore.YELLOW}PCC B7 Signal Data:{Style.RESET_ALL}")
            for field_name, value in signal_data['pcc_b7'].items():
                if value != "N/A":
                    numeric_value = self.extract_numeric_value(value)
                    if numeric_value is not None:
                        metric_type = None
                        if 'RSSI' in field_name:
                            metric_type = 'rssi'
                        elif 'RSRP' in field_name:
                            metric_type = 'rsrp'
                        elif 'RSRQ' in field_name:
                            metric_type = 'rsrq'
                        elif 'SINR' in field_name:
                            metric_type = 'sinr'
                        
                        if metric_type:
                            color = self.get_signal_quality_color(metric_type, numeric_value)
                            quality = self.get_signal_quality_text(metric_type, numeric_value)
                            print(f"  {Fore.WHITE}{field_name:25}{Style.RESET_ALL} {color}{value:10}{Style.RESET_ALL} ({quality})")
                        else:
                            print(f"  {Fore.WHITE}{field_name:25}{Style.RESET_ALL} {Fore.CYAN}{value:10}{Style.RESET_ALL}")
                    else:
                        print(f"  {Fore.WHITE}{field_name:25}{Style.RESET_ALL} {Fore.CYAN}{value:10}{Style.RESET_ALL}")
        else:
            # Full PCC B7 display
            print(f"\n{Fore.YELLOW}PCC B7 SIGNAL DATA{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}{'-'*30}{Style.RESET_ALL}")
            
            for field_name, value in signal_data['pcc_b7'].items():
                if value == "N/A":
                    print(f"{Fore.WHITE}{field_name:25}: {Fore.RED}N/A{Style.RESET_ALL}")
                    continue
                
                # Extract numeric value for color coding
                numeric_value = self.extract_numeric_value(value)
                if numeric_value is not None:
                    # Determine metric type for appropriate threshold checking
                    metric_type = None
                    if 'RSSI' in field_name:
                        metric_type = 'rssi'
                    elif 'RSRP' in field_name:
                        metric_type = 'rsrp'
                    elif 'RSRQ' in field_name:
                        metric_type = 'rsrq'
                    elif 'SINR' in field_name:
                        metric_type = 'sinr'
                    
                    if metric_type:
                        color = self.get_signal_quality_color(metric_type, numeric_value)
                        quality = self.get_signal_quality_text(metric_type, numeric_value)
                        print(f"{Fore.WHITE}{field_name:25}: {color}{value:10} ({quality}){Style.RESET_ALL}")
                    else:
                        print(f"{Fore.WHITE}{field_name:25}: {Fore.CYAN}{value}{Style.RESET_ALL}")
                else:
                    print(f"{Fore.WHITE}{field_name:25}: {Fore.CYAN}{value}{Style.RESET_ALL}")
        
        if compact_mode:
            # Compact PSCC N78 display in table format
            print(f"\n{Fore.YELLOW}PSCC N78 Signal Data:{Style.RESET_ALL}")
            for field_name, value in signal_data['pscc_n78'].items():
                if value != "N/A":
                    numeric_value = self.extract_numeric_value(value)
                    if numeric_value is not None:
                        metric_type = None
                        if 'RSRP' in field_name:
                            metric_type = 'rsrp'
                        elif 'RSRQ' in field_name:
                            metric_type = 'rsrq'
                        elif 'SINR' in field_name:
                            metric_type = 'sinr'
                        
                        if metric_type:
                            color = self.get_signal_quality_color(metric_type, numeric_value)
                            quality = self.get_signal_quality_text(metric_type, numeric_value)
                            print(f"  {Fore.WHITE}{field_name:25}{Style.RESET_ALL} {color}{value:10}{Style.RESET_ALL} ({quality})")
                        else:
                            print(f"  {Fore.WHITE}{field_name:25}{Style.RESET_ALL} {Fore.CYAN}{value:10}{Style.RESET_ALL}")
                    else:
                        print(f"  {Fore.WHITE}{field_name:25}{Style.RESET_ALL} {Fore.CYAN}{value:10}{Style.RESET_ALL}")
        else:
            # Full PSCC N78 display
            print(f"\n{Fore.YELLOW}PSCC N78 SIGNAL DATA{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}{'-'*30}{Style.RESET_ALL}")
            
            for field_name, value in signal_data['pscc_n78'].items():
                if value == "N/A":
                    print(f"{Fore.WHITE}{field_name:25}: {Fore.RED}N/A{Style.RESET_ALL}")
                    continue
                
                # Extract numeric value for color coding
                numeric_value = self.extract_numeric_value(value)
                if numeric_value is not None:
                    # Determine metric type for appropriate threshold checking
                    metric_type = None
                    if 'RSRP' in field_name:
                        metric_type = 'rsrp'
                    elif 'RSRQ' in field_name:
                        metric_type = 'rsrq'
                    elif 'SINR' in field_name:
                        metric_type = 'sinr'
                    
                    if metric_type:
                        color = self.get_signal_quality_color(metric_type, numeric_value)
                        quality = self.get_signal_quality_text(metric_type, numeric_value)
                        print(f"{Fore.WHITE}{field_name:25}: {color}{value:10} ({quality}){Style.RESET_ALL}")
                    else:
                        print(f"{Fore.WHITE}{field_name:25}: {Fore.CYAN}{value}{Style.RESET_ALL}")
                else:
                    print(f"{Fore.WHITE}{field_name:25}: {Fore.CYAN}{value}{Style.RESET_ALL}")
        
        if not compact_mode:
            # Display signal quality legend (only in full mode)
            print(f"\n{Fore.YELLOW}SIGNAL QUALITY LEGEND{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}{'-'*30}{Style.RESET_ALL}")
            print(f"{Fore.GREEN}Excellent{Style.RESET_ALL} - Strong signal, optimal performance")
            print(f"{Fore.YELLOW}Good{Style.RESET_ALL} - Good signal, reliable connection")
            print(f"{Fore.YELLOW}Fair{Style.RESET_ALL} - Acceptable signal, may have minor issues")
            print(f"{Fore.RED}Poor{Style.RESET_ALL} - Weak signal, consider repositioning router")
        else:
            # Compact legend for monitoring
            print(f"\n{Fore.YELLOW}Legend:{Style.RESET_ALL} {Fore.GREEN}Excellent{Style.RESET_ALL} | {Fore.YELLOW}Good/Fair{Style.RESET_ALL} | {Fore.RED}Poor{Style.RESET_ALL}")
        
        if not compact_mode:
            # Display metric explanations (only in full mode)
            print(f"\n{Fore.YELLOW}SIGNAL METRICS EXPLANATION{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}{'-'*30}{Style.RESET_ALL}")
            print(f"{Fore.WHITE}RSSI (Received Signal Strength Indicator):{Style.RESET_ALL}")
            print("  Measures the power level of the received signal. Higher values (closer to 0) are better.")
            print(f"{Fore.WHITE}RSRP (Reference Signal Received Power):{Style.RESET_ALL}")
            print("  Measures the power level of reference signals. Higher values (closer to 0) are better.")
            print(f"{Fore.WHITE}RSRQ (Reference Signal Received Quality):{Style.RESET_ALL}")
            print("  Measures the quality of the received signal. Higher values (closer to 0) are better.")
            print(f"{Fore.WHITE}SINR (Signal-to-Interference-plus-Noise Ratio):{Style.RESET_ALL}")
            print("  Measures signal quality relative to noise and interference. Higher values are better.")
    
    def start_monitoring(self, refresh_interval=30):
        """Start continuous monitoring of signal data."""
        try:
            if not self.setup_driver():
                return False
            
            if not self.login():
                return False
            
            if not self.navigate_to_network_info():
                return False
            
            print(f"{Fore.GREEN}Starting signal monitoring...{Style.RESET_ALL}")
            print(f"{Fore.CYAN}Refresh interval: {refresh_interval} seconds{Style.RESET_ALL}")
            print(f"{Fore.CYAN}Press Ctrl+C to stop monitoring{Style.RESET_ALL}")
            
            while True:
                try:
                    signal_data = self.parse_signal_data()
                    self.display_signal_data(signal_data, clear_screen=True, compact_mode=True)
                    
                    # Determine overall signal quality and play audio feedback
                    if self.audio_feedback:
                        overall_quality = self.get_overall_signal_quality(signal_data)
                        self.play_audio(overall_quality)
                        print(f"\n{Fore.MAGENTA}Audio Feedback: {overall_quality.upper()} signal quality{Style.RESET_ALL}")
                    
                    # Save data to JSON file
                    with open('signal_data.json', 'w', encoding='utf-8') as f:
                        json.dump(signal_data, f, indent=2)
                    
                    # Show status line
                    print(f"\n{Fore.CYAN}Monitoring active - Next update in {refresh_interval}s | Press Ctrl+C to stop{Style.RESET_ALL}")
                    time.sleep(refresh_interval)
                    
                except KeyboardInterrupt:
                    print(f"\n{Fore.YELLOW}Monitoring stopped by user{Style.RESET_ALL}")
                    break
                except Exception as e:
                    self.logger.error("Error during monitoring: %s", e)
                    print(f"{Fore.RED}Error occurred: {e}{Style.RESET_ALL}")
                    print(f"{Fore.CYAN}Retrying in {refresh_interval} seconds...{Style.RESET_ALL}")
                    time.sleep(refresh_interval)
            
            return True
            
        except Exception as e:
            self.logger.error("Monitoring failed: %s", e)
            return False
        finally:
            if self.driver:
                self.driver.quit()
                self.logger.info("WebDriver closed")
    
    def get_single_reading(self):
        """Get a single signal reading."""
        try:
            if not self.setup_driver():
                return None
            
            if not self.login():
                return None
            
            if not self.navigate_to_network_info():
                return None
            
            signal_data = self.parse_signal_data()
            self.display_signal_data(signal_data, clear_screen=False, compact_mode=False)
            
            return signal_data
            
        except Exception as e:
            self.logger.error("Failed to get signal reading: %s", e)
            return None
        finally:
            if self.driver:
                self.driver.quit()


def main():
    """Main function to run the signal tracker."""
    print(f"{Fore.CYAN}Zyxel NR2301 5G Router Signal Tracker{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'='*50}{Style.RESET_ALL}")
    
    # Check for debug mode
    debug_mode = "--debug" in sys.argv or "-d" in sys.argv
    
    if debug_mode:
        print(f"{Fore.YELLOW}DEBUG MODE ENABLED - Browser will be visible{Style.RESET_ALL}")
    
    # Check for audio feedback mode
    audio_feedback = "--no-audio" not in sys.argv and "-na" not in sys.argv
    
    if not audio_feedback:
        print(f"{Fore.YELLOW}AUDIO FEEDBACK DISABLED{Style.RESET_ALL}")
    
    # Initialize tracker (credentials will be prompted if not provided)
    tracker = RouterSignalTracker(debug_mode=debug_mode, audio_feedback=audio_feedback)
    
    try:
        while True:
            print(f"\n{Fore.YELLOW}Choose an option:{Style.RESET_ALL}")
            print(f"{Fore.WHITE}1. Get single signal reading{Style.RESET_ALL}")
            print(f"{Fore.WHITE}2. Start continuous monitoring{Style.RESET_ALL}")
            print(f"{Fore.WHITE}3. Test login only (debug){Style.RESET_ALL}")
            print(f"{Fore.WHITE}4. Toggle audio feedback (currently: {'ON' if tracker.audio_feedback else 'OFF'}){Style.RESET_ALL}")
            print(f"{Fore.WHITE}5. Exit{Style.RESET_ALL}")
            
            choice = input(f"\n{Fore.CYAN}Enter your choice (1-5): {Style.RESET_ALL}").strip()
            
            if choice == '1':
                tracker.get_single_reading()
            elif choice == '2':
                interval = input(f"{Fore.CYAN}Enter refresh interval in seconds (default 30): {Style.RESET_ALL}").strip()
                try:
                    interval = int(interval) if interval else 30
                except ValueError:
                    interval = 30
                tracker.start_monitoring(interval)
            elif choice == '3':
                print(f"{Fore.YELLOW}Testing login process...{Style.RESET_ALL}")
                if tracker.setup_driver():
                    if tracker.login():
                        print(f"{Fore.GREEN}Login successful!{Style.RESET_ALL}")
                        print(f"{Fore.CYAN}Current URL: {tracker.driver.current_url}{Style.RESET_ALL}")
                        print(f"{Fore.CYAN}Page Title: {tracker.driver.title}{Style.RESET_ALL}")
                    else:
                        print(f"{Fore.RED}Login failed{Style.RESET_ALL}")
                    tracker.driver.quit()
                else:
                    print(f"{Fore.RED}Failed to initialize WebDriver{Style.RESET_ALL}")
            elif choice == '4':
                tracker.audio_feedback = not tracker.audio_feedback
                status = "ON" if tracker.audio_feedback else "OFF"
                print(f"{Fore.GREEN}Audio feedback is now {status}{Style.RESET_ALL}")
            elif choice == '5':
                print(f"{Fore.GREEN}Goodbye!{Style.RESET_ALL}")
                break
            else:
                print(f"{Fore.RED}Invalid choice. Please enter 1, 2, 3, 4, or 5.{Style.RESET_ALL}")
    
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Goodbye!{Style.RESET_ALL}")
    except Exception as e:
        print(f"{Fore.RED}Unexpected error: {e}{Style.RESET_ALL}")


if __name__ == "__main__":
    main()
    