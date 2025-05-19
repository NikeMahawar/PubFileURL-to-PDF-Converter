#!/usr/bin/env python3
"""
PubHTML5 Screenshot Utility

A tool to capture screenshots of PubHTML5 books.
"""

import argparse
import base64
import logging
import os
import random
import sys
import time
from io import BytesIO
from typing import Dict, List, Optional, Tuple

from PIL import Image
from flask import Flask, request, jsonify
from threading import Thread

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("pubhtml5_screenshot.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Try importing Selenium components
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.common.exceptions import (
        TimeoutException, WebDriverException, NoSuchElementException,
        ElementNotVisibleException, ElementNotInteractableException
    )

    SELENIUM_AVAILABLE = True
except ImportError:
    logger.warning("Selenium not available. Browser automation methods will be disabled.")
    SELENIUM_AVAILABLE = False


class NetworkUtils:
    """Utility class for network operations"""

    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ]

    @staticmethod
    def get_random_user_agent() -> str:
        """Get a random user agent string"""
        return random.choice(NetworkUtils.USER_AGENTS)


class BrowserCapture:
    """Capture screenshots using browser automation with Selenium"""

    def __init__(self, url: str, output_dir: str):
        self.url = url
        self.output_dir = output_dir
        self.driver = None

    def setup_driver(self, headless: bool = True) -> bool:
        """Set up and configure Chrome WebDriver"""
        if not SELENIUM_AVAILABLE:
            logger.error("Selenium is not available. Cannot use browser automation.")
            return False

        try:
            chrome_options = Options()
            if headless:
                chrome_options.add_argument("--headless=new")

            # Basic settings for stability
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")

            # Anti-detection measures
            chrome_options.add_argument("--disable-blink-features=AutomationControlled")
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_options.add_experimental_option("useAutomationExtension", False)

            # Random user agent
            chrome_options.add_argument(f"--user-agent={NetworkUtils.get_random_user_agent()}")

            # Additional stability settings
            chrome_options.add_argument("--disable-extensions")
            chrome_options.add_argument("--disable-infobars")
            chrome_options.add_argument("--disable-notifications")

            self.driver = webdriver.Chrome(options=chrome_options)
            self.driver.set_window_size(1920, 1080)

            # Anti-detection JavaScript
            self.driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            logger.info("Successfully initialized Chrome WebDriver")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Chrome WebDriver: {str(e)}")
            return False

    def find_book_element(self) -> Optional[webdriver.remote.webelement.WebElement]:
        """Try to find the book container element"""
        if not self.driver:
            return None

        # List of selectors to try
        selectors = [
            # Canvas selectors (highest priority)
            (By.ID, "cvsOBJ"),
            (By.CSS_SELECTOR, "canvas#cvsOBJ"),
            (By.TAG_NAME, "canvas"),

            # Container selectors
            (By.CLASS_NAME, "flipbook-container"),
            (By.CLASS_NAME, "flipbook"),
            (By.ID, "flipbook"),

            # Content selectors
            (By.CSS_SELECTOR, ".pages"),
            (By.CSS_SELECTOR, ".page-wrapper"),
            (By.CSS_SELECTOR, ".page.current"),

            # Generic container selectors
            (By.CSS_SELECTOR, "div[class*='book']"),
            (By.CSS_SELECTOR, "div[id*='book']"),

            # Last resort - the entire viewport area
            (By.TAG_NAME, "body")
        ]

        for selector_type, selector_value in selectors:
            try:
                elements = self.driver.find_elements(selector_type, selector_value)
                for element in elements:
                    if element.is_displayed():
                        size = element.size
                        # Check if element has reasonable size
                        if size['width'] > 200 and size['height'] > 200:
                            logger.info(f"Found book element using {selector_type}: {selector_value}")
                            return element
            except Exception:
                continue

        logger.warning("Could not find any suitable book element")
        return None

    def navigate_to_next_page(self) -> bool:
        """Try multiple methods to navigate to the next page"""
        if not self.driver:
            return False

        # Method 1: Try clicking on next/right buttons
        button_selectors = [
            (By.CLASS_NAME, "bttnRight"),
            (By.CLASS_NAME, "rightBtn"),
            (By.CSS_SELECTOR, "[class*='right'][class*='btn']"),
            (By.CSS_SELECTOR, "[class*='next']"),
            (By.CSS_SELECTOR, "button:contains('Next')"),
            (By.XPATH, "//button[contains(text(), 'Next')]"),
            (By.XPATH, "//span[contains(text(), 'Next')]"),
            (By.XPATH, "//a[contains(text(), 'Next')]"),
        ]

        for selector_type, selector_value in button_selectors:
            try:
                elements = self.driver.find_elements(selector_type, selector_value)
                for element in elements:
                    if element.is_displayed():
                        try:
                            # Try regular click
                            element.click()
                            logger.info(f"Navigated using button: {selector_value}")
                            return True
                        except:
                            # Try JavaScript click
                            try:
                                self.driver.execute_script("arguments[0].click();", element)
                                logger.info(f"Navigated using JS click: {selector_value}")
                                return True
                            except:
                                continue
            except Exception:
                continue

        # Method 2: Try using arrow keys
        try:
            body = self.driver.find_element(By.TAG_NAME, "body")
            ActionChains(self.driver).move_to_element(body).send_keys(Keys.ARROW_RIGHT).perform()
            logger.info("Navigated using right arrow key")
            return True
        except Exception:
            # Try JavaScript key event
            try:
                self.driver.execute_script(
                    "document.dispatchEvent(new KeyboardEvent('keydown', {'key': 'ArrowRight', 'keyCode': 39}))"
                )
                logger.info("Navigated using JavaScript key event")
                return True
            except Exception:
                pass

        # Method 3: Try using page input field
        try:
            input_elements = self.driver.find_elements(
                By.XPATH, "//input[@type='text' and contains(@class, 'page')] | " +
                          "//input[contains(@id, 'page')]"
            )

            for input_elem in input_elements:
                if input_elem.is_displayed():
                    current_value = input_elem.get_attribute("value")
                    try:
                        current_page = int(current_value)
                        next_page = current_page + 1

                        input_elem.clear()
                        input_elem.send_keys(str(next_page))
                        input_elem.send_keys(Keys.ENTER)

                        logger.info(f"Navigated to page {next_page} using input field")
                        return True
                    except:
                        continue
        except Exception:
            pass

        # Method 4: Try various JavaScript methods
        js_methods = [
            "if(typeof nextPage === 'function') nextPage();",
            "if(typeof goToNextPage === 'function') goToNextPage();",
            "if(typeof flipNext === 'function') flipNext();",
            "if(typeof rightButtonClick === 'function') rightButtonClick();",
            "if(typeof turnPageRight === 'function') turnPageRight();",
            "if(window.viewer && typeof window.viewer.next === 'function') window.viewer.next();",
            "if(window.book && typeof window.book.turnToNextPage === 'function') window.book.turnToNextPage();",
            "document.querySelector('.rightBtn')?.click();",
            "document.querySelector('.bttnRight')?.click();",
            "document.querySelector('[class*=\"right\"][class*=\"btn\"]')?.click();"
        ]

        for js in js_methods:
            try:
                self.driver.execute_script(js)
                logger.info(f"Navigated using JavaScript method")
                return True
            except Exception:
                continue

        logger.warning("All navigation methods failed")
        return False

    def capture_screenshot(self, page_num: int) -> Optional[str]:
        """Capture the current page as an image"""
        if not self.driver:
            return None

        try:
            # First try to find book element
            book_element = self.find_book_element()

            if book_element:
                # Take screenshot of just the book element
                logger.info(f"Taking screenshot of book element for page {page_num}")
                screenshot = book_element.screenshot_as_base64
                screenshot_data = base64.b64decode(screenshot)
            else:
                # Fall back to full page screenshot
                logger.info(f"Taking full page screenshot for page {page_num}")
                screenshot = self.driver.get_screenshot_as_base64()
                screenshot_data = base64.b64decode(screenshot)

            # Save image temporarily
            image = Image.open(BytesIO(screenshot_data))
            img_path = os.path.join(self.output_dir, f"page_{page_num:03d}.png")
            image.save(img_path)

            logger.debug(f"Saved page {page_num} screenshot to {img_path}")
            return img_path

        except Exception as e:
            logger.error(f"Error capturing page {page_num}: {str(e)}")
            return None

    def capture_pages(self, max_pages: int = 200) -> List[str]:
        """Capture multiple pages"""
        # Initialize WebDriver
        if not self.setup_driver(headless=True):
            logger.warning("Trying with GUI mode instead of headless")
            if not self.setup_driver(headless=False):
                logger.error("Failed to initialize WebDriver in both modes")
                return []

        captured_images = []

        try:
            # Load the book URL
            self.driver.get(self.url)
            logger.info(f"Loaded URL: {self.url}")

            # Wait for the content to load
            time.sleep(10)

            # Try to navigate to the first page
            try:
                first_page_selectors = [
                    (By.XPATH, "//div[contains(@class, 'first')]"),
                    (By.XPATH, "//span[contains(@class, 'first')]"),
                    (By.XPATH, "//a[contains(@class, 'first')]"),
                    (By.XPATH, "//button[contains(@class, 'first')]")
                ]

                for selector_type, selector_value in first_page_selectors:
                    try:
                        elements = self.driver.find_elements(selector_type, selector_value)
                        for element in elements:
                            if element.is_displayed():
                                element.click()
                                time.sleep(2)
                                logger.info("Navigated to first page")
                                break
                    except Exception:
                        continue
            except Exception:
                # Try using page number input
                try:
                    input_elements = self.driver.find_elements(
                        By.XPATH, "//input[@type='text' and contains(@class, 'page')] | " +
                                  "//input[contains(@id, 'page')]"
                    )

                    for input_elem in input_elements:
                        if input_elem.is_displayed():
                            input_elem.clear()
                            input_elem.send_keys("1")
                            input_elem.send_keys(Keys.ENTER)
                            time.sleep(2)
                            logger.info("Navigated to first page using input")
                            break
                except Exception:
                    pass

            # Start capturing pages
            # First, verify we can capture at least one page
            first_page = self.capture_screenshot(1)
            if not first_page:
                logger.error("Failed to capture the first page")
                return []

            captured_images.append(first_page)

            # Now capture remaining pages
            for page_num in range(2, max_pages + 1):
                # Navigate to next page
                if not self.navigate_to_next_page():
                    logger.info(f"Reached the end of the content at page {page_num - 1}")
                    break

                # Wait for page to load
                time.sleep(2)

                # Capture the page
                img_path = self.capture_screenshot(page_num)
                if img_path:
                    captured_images.append(img_path)

                    # Log progress
                    if page_num % 5 == 0:
                        logger.info(f"Captured {page_num} pages so far")
                else:
                    logger.warning(f"Failed to capture page {page_num}")

            logger.info(f"Successfully captured {len(captured_images)} pages")
            return captured_images

        except Exception as e:
            logger.error(f"Error during capture: {str(e)}")
            return captured_images

        finally:
            # Clean up
            if self.driver:
                try:
                    self.driver.quit()
                except Exception:
                    pass


class ScreenshotUtility:
    """Main class for capturing screenshots"""

    def __init__(self, url: str, output_dir: str = "screenshots", max_pages: int = 200):
        self.url = url
        self.output_dir = output_dir
        self.max_pages = max_pages

        # Create output directory if it doesn't exist
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    def capture(self) -> List[str]:
        """Main method to capture screenshots"""
        logger.info(f"Starting capture for URL: {self.url}")

        browser_capture = BrowserCapture(self.url, self.output_dir)
        captured_images = browser_capture.capture_pages(self.max_pages)

        if captured_images:
            logger.info(f"Successfully captured {len(captured_images)} screenshots to {self.output_dir}")
        else:
            logger.error("Failed to capture any screenshots")

        return captured_images


def main():
    """Main function to run the script"""
    parser = argparse.ArgumentParser(description='Capture screenshots from web content')
    parser.add_argument('--url', type=str, required=True,
                        help='URL of the content to capture')
    parser.add_argument('--output-dir', type=str, default="screenshots",
                        help='Output directory for screenshots')
    parser.add_argument('--max-pages', type=int, default=200,
                        help='Maximum number of pages to capture')
    parser.add_argument('--verbose', action='store_true',
                        help='Enable verbose logging')

    args = parser.parse_args()

    # Set logging level
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    print(f"Screenshot Utility")
    print(f"------------------------")
    print(f"URL: {args.url}")
    print(f"Output Directory: {args.output_dir}")
    print(f"Max Pages: {args.max_pages}")
    print()

    # Start capture
    utility = ScreenshotUtility(
        args.url,
        args.output_dir,
        args.max_pages
    )

    captured_images = utility.capture()

    if captured_images:
        print(f"\nSuccess! Captured {len(captured_images)} screenshots to: {args.output_dir}")
        return 0
    else:
        print(f"\nFailed to capture screenshots. Check the log file for details.")
        return 1


# Initialize Flask app
app = Flask(__name__)


# Function to handle the capture in a separate thread
def capture_screenshots(url, output_dir):
    utility = ScreenshotUtility(url, output_dir)
    return utility.capture()


@app.route('/')
def index():
    return '''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Screenshot Utility</title>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/css/all.min.css">
        <style>
            body {
                font-family: Arial, sans-serif;
                background-color: #f4f4f4;
                text-align: center;
                padding: 50px;
                max-width: 800px;
                margin: 0 auto;
            }
            .container {
                background-color: white;
                border-radius: 8px;
                padding: 30px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            input[type="text"] {
                width: 80%;
                padding: 12px;
                margin: 15px 0;
                border: 1px solid #ddd;
                border-radius: 4px;
                font-size: 16px;
            }
            button {
                padding: 12px 25px;
                background-color: #28a745;
                color: white;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-size: 16px;
                transition: background-color 0.3s;
            }
            button:hover {
                background-color: #218838;
            }
            button:disabled {
                background-color: #95c9a2;
                cursor: not-allowed;
            }
            #loader {
                display: none;
                margin-top: 30px;
                padding: 20px;
                background-color: #f8f9fa;
                border-radius: 8px;
            }
            .spinner {
                margin-bottom: 15px;
                font-size: 30px;
                color: #28a745;
            }
            .progress-container {
                width: 100%;
                background-color: #e0e0e0;
                border-radius: 5px;
                margin: 20px 0;
                height: 25px;
                position: relative;
            }
            .progress-bar {
                height: 100%;
                background-color: #28a745;
                border-radius: 5px;
                width: 0%;
                transition: width 1s;
                display: flex;
                align-items: center;
                justify-content: center;
                color: white;
                font-weight: bold;
            }
            .time-remaining {
                margin-top: 10px;
                color: #666;
            }
            .capture-status {
                margin-top: 15px;
                font-weight: bold;
            }
            .completion-message {
                display: none;
                margin-top: 20px;
                padding: 15px;
                background-color: #d4edda;
                color: #155724;
                border-radius: 4px;
            }
            .error-message {
                display: none;
                margin-top: 20px;
                padding: 15px;
                background-color: #f8d7da;
                color: #721c24;
                border-radius: 4px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Web Screenshot Utility</h1>
            <p>Enter the URL of the web content you want to capture</p>

            <div>
                <input type="text" id="url" placeholder="Enter the URL" required>
                <button id="captureBtn">Capture</button>
            </div>

            <div id="loader">
                <div class="spinner">
                    <i class="fas fa-spinner fa-spin"></i>
                </div>
                <h3>Capturing Screenshots...</h3>
                <p>Please wait while we process your request. This may take several minutes.</p>

                <div class="progress-container">
                    <div class="progress-bar" id="progressBar">0%</div>
                </div>

                <div class="time-remaining" id="timeRemaining">Estimated time remaining: 8 minutes</div>
                <div class="capture-status" id="captureStatus">Initializing capture...</div>

                <p><b>Note:</b> Please do not close this window during the process.</p>
            </div>

            <div class="completion-message" id="completionMessage">

            </div>

            <div class="error-message" id="errorMessage">
                <i class="fas fa-exclamation-circle"></i> Error during capture. Please try again later.
            </div>
        </div>

        <script>
            document.getElementById('captureBtn').onclick = function() {
                const url = document.getElementById('url').value.trim();

                if (!url) {
                    alert("Please enter a valid URL");
                    return;
                }

                // Show loader and disable input/button
                document.getElementById('loader').style.display = 'block';
                document.getElementById('url').disabled = true;
                document.getElementById('captureBtn').disabled = true;
                document.getElementById('completionMessage').style.display = 'none';
                document.getElementById('errorMessage').style.display = 'none';

                // Start progress simulation
                simulateProgress();

                // Send capture request
                fetch('/capture', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                    },
                    body: new URLSearchParams({ url: url })
                })
                .then(response => response.json())
                .then(data => {
                    // After response, show completion
                    setTimeout(() => {
                        document.getElementById('captureStatus').textContent = 'Capture complete!';
                        document.getElementById('progressBar').style.width = '100%';
                        document.getElementById('progressBar').textContent = '100%';
                        document.getElementById('timeRemaining').textContent = 'Finished!';
                        document.getElementById('completionMessage').style.display = 'block';

                        // Re-enable controls
                        document.getElementById('url').disabled = false;
                        document.getElementById('captureBtn').disabled = false;
                    }, 1000);
                })
                .catch(error => {
                    console.error('Error:', error);
                    document.getElementById('errorMessage').style.display = 'block';
                    document.getElementById('url').disabled = false;
                    document.getElementById('captureBtn').disabled = false;
                });
            };

            function simulateProgress() {
                let progress = 0;
                let remainingTime = 540; // 5 minutes in seconds
                const totalTime = remainingTime;

                const progressBar = document.getElementById('progressBar');
                const timeRemainingElem = document.getElementById('timeRemaining');
                const captureStatusElem = document.getElementById('captureStatus');

                const statusMessages = [
                    "Initializing capture...",
                    "Loading web page...",
                    "Finding content elements...",
                    "Capturing screenshot of page 1...",
                    "Navigating to next page...",
                    "Processing screenshots...",
                    "Saving screenshots...",
                    "Finalizing capture..."
                ];

                // Update every second
                const interval = setInterval(() => {
                    remainingTime--;

                    // Calculate progress based on time elapsed
                    // Make it non-linear to simulate real capture process
                    const timeProgress = (totalTime - remainingTime) / totalTime;

                    // Ensure progress doesn't reach 100% during simulation
                    progress = Math.min(Math.pow(timeProgress, 0.7) * 95, 95);

                    // Update progress bar
                    progressBar.style.width = progress + '%';
                    progressBar.textContent = Math.round(progress) + '%';

                    // Format remaining time
                    const minutes = Math.floor(remainingTime / 60);
                    const seconds = remainingTime % 60;
                    timeRemainingElem.textContent = `Estimated time remaining: ${minutes}:${seconds < 10 ? '0' : ''}${seconds}`;

                    // Update status message
                    if (timeProgress > 0.9) {
                        captureStatusElem.textContent = statusMessages[7];
                    } else if (timeProgress > 0.75) {
                        captureStatusElem.textContent = statusMessages[6];
                    } else if (timeProgress > 0.6) {
                        captureStatusElem.textContent = statusMessages[5];
                    } else if (timeProgress > 0.4) {
                        captureStatusElem.textContent = statusMessages[4];
                    } else if (timeProgress > 0.25) {
                        captureStatusElem.textContent = statusMessages[3];
                    } else if (timeProgress > 0.1) {
                        captureStatusElem.textContent = statusMessages[2];
                    } else if (timeProgress > 0.05) {
                        captureStatusElem.textContent = statusMessages[1];
                    }

                    // Stop if time is up
                    if (remainingTime <= 0) {
                        clearInterval(interval);
                    }
                }, 1000);
            }
        </script>
    </body>
    </html>
    '''


@app.route('/capture', methods=['POST'])
def capture():
    url = request.form['url']
    output_dir = "screenshots"  # You can customize this as needed

    # Start the capture in a separate thread
    thread = Thread(target=capture_screenshots, args=(url, output_dir))
    thread.start()

    return jsonify({"message": "Screenshot capture started! Please check back later."})


if __name__ == "__main__":
    app.run(debug=True)