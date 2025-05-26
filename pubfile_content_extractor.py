#!/usr/bin/env python3
"""
PubHTML5 Book Downloader - Screenshot Only Version with Real-time Progress

A tool to download PubHTML5 books using browser screenshots with live progress updates.
"""

import argparse
import base64
import logging
import os
import sys
import time
import uuid
from io import BytesIO
from typing import List, Optional, Tuple

from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from flask import Flask, request, jsonify, send_file
from threading import Thread
import json

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("pubhtml5_downloader.log"),
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
    logger.error("Selenium not available. This tool requires Selenium to work.")
    SELENIUM_AVAILABLE = False
    sys.exit(1)

# Global dictionary to store download progress
download_progress = {}


class BrowserExtractor:
    """Extract book pages using browser automation with Selenium"""

    def __init__(self, book_url: str, temp_dir: str, session_id: str):
        self.book_url = book_url
        self.temp_dir = temp_dir
        self.session_id = session_id
        self.driver = None

    def update_progress(self, current_page: int, total_pages: int = None, status: str = ""):
        """Update the progress for this session"""
        global download_progress
        download_progress[self.session_id] = {
            'current_page': current_page,
            'total_pages': total_pages,
            'status': status,
            'completed': False,
            'error': False
        }

    def setup_driver(self, headless: bool = True) -> bool:
        """Set up and configure Chrome WebDriver"""
        try:
            self.update_progress(0, status="Initializing browser...")

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
            self.update_progress(0, status=f"Error initializing browser: {str(e)}")
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

    def capture_current_page(self, page_num: int) -> Optional[str]:
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
            temp_img_path = os.path.join(self.temp_dir, f"page_{page_num:03d}.png")
            image.save(temp_img_path)

            logger.debug(f"Saved page {page_num} screenshot to {temp_img_path}")
            return temp_img_path

        except Exception as e:
            logger.error(f"Error capturing page {page_num}: {str(e)}")
            return None

    def extract_pages(self) -> List[Tuple[int, str]]:
        """Extract pages using browser automation"""
        # Initialize WebDriver
        if not self.setup_driver(headless=True):
            logger.warning("Trying with GUI mode instead of headless")
            if not self.setup_driver(headless=False):
                logger.error("Failed to initialize WebDriver in both modes")
                self.update_progress(0, status="Failed to initialize browser")
                return []

        try:
            # Load the book URL
            self.update_progress(0, status="Loading book page...")
            self.driver.get(self.book_url)
            logger.info(f"Loaded book URL: {self.book_url}")

            # Wait for the book to load
            self.update_progress(0, status="Waiting for book to load...")
            time.sleep(15)

            # Try to navigate to the first page
            self.update_progress(0, status="Locating first page...")
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
            pages = []
            max_pages = 300

            # First, verify we can capture at least one page
            self.update_progress(1, status="Capturing first page...")
            first_page = self.capture_current_page(1)
            if not first_page:
                logger.error("Failed to capture the first page")
                self.update_progress(0, status="Failed to capture first page")
                return []

            pages.append((1, first_page))

            # Now capture remaining pages
            for page_num in range(2, max_pages + 1):
                self.update_progress(page_num, status=f"Capturing page {page_num}...")

                # Navigate to next page
                if not self.navigate_to_next_page():
                    logger.info(f"Reached the end of the book at page {page_num - 1}")
                    self.update_progress(page_num - 1, page_num - 1, f"Completed! Captured {page_num - 1} pages")
                    break

                # Wait for page to load
                time.sleep(2)

                # Capture the page
                img_path = self.capture_current_page(page_num)
                if img_path:
                    pages.append((page_num, img_path))

                    # Log progress every 5 pages
                    if page_num % 5 == 0:
                        logger.info(f"Captured {page_num} pages so far")
                else:
                    logger.warning(f"Failed to capture page {page_num}")

            logger.info(f"Successfully extracted {len(pages)} pages using browser automation")
            return pages

        except Exception as e:
            logger.error(f"Error in browser extraction: {str(e)}")
            self.update_progress(0, status=f"Error during extraction: {str(e)}")
            return []

        finally:
            # Clean up
            if self.driver:
                try:
                    self.driver.quit()
                except Exception:
                    pass


class PdfGenerator:
    """Generate PDF from extracted images"""

    def __init__(self, output_pdf: str, temp_dir: str, session_id: str):
        self.output_pdf = output_pdf
        self.temp_dir = temp_dir
        self.session_id = session_id

    def update_progress(self, status: str):
        """Update the progress for this session"""
        global download_progress
        if self.session_id in download_progress:
            download_progress[self.session_id]['status'] = status

    def create_pdf(self, pages: List[Tuple[int, str]]) -> bool:
        """Create PDF file from extracted pages"""
        if not pages:
            logger.error("No pages to create PDF")
            self.update_progress("Error: No pages to create PDF")
            return False

        try:
            self.update_progress(f"Creating PDF with {len(pages)} pages...")
            logger.info(f"Creating PDF with {len(pages)} pages")

            # Create PDF
            c = canvas.Canvas(self.output_pdf, pagesize=letter)
            page_width, page_height = letter

            for i, (page_num, img_path) in enumerate(pages):
                try:
                    self.update_progress(f"Adding page {page_num} to PDF...")

                    # Open image
                    image = Image.open(img_path)

                    # Calculate dimensions
                    img_width, img_height = image.size
                    aspect = img_height / img_width

                    # Calculate dimensions to fit on PDF page with margins
                    width = page_width - 50
                    height = width * aspect

                    # If image is too tall, scale it down
                    if height > page_height - 50:
                        height = page_height - 50
                        width = height / aspect

                    # Add image to PDF
                    c.drawImage(img_path, 25, page_height - height - 25, width=width, height=height)
                    c.showPage()

                except Exception as e:
                    logger.error(f"Error adding page {page_num} to PDF: {str(e)}")

            # Save PDF
            self.update_progress("Finalizing PDF...")
            c.save()
            logger.info(f"PDF successfully saved to {self.output_pdf}")
            return True

        except Exception as e:
            logger.error(f"Error creating PDF: {str(e)}")
            self.update_progress(f"Error creating PDF: {str(e)}")
            return False


class PubHTML5Downloader:
    """Main class for downloading PubHTML5 books using screenshots only"""

    def __init__(self, url: str, output_pdf: str, session_id: str):
        self.url = url
        self.output_pdf = output_pdf
        self.session_id = session_id
        self.temp_dir = f"pubhtml5_temp_{session_id}"

        # Create temp directory if it doesn't exist
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)

    def download(self) -> bool:
        """Main method to download the book"""
        global download_progress

        logger.info(f"Starting screenshot-based download for book: {self.url}")

        # Use browser automation to capture screenshots
        logger.info("Using browser automation method to capture screenshots")
        try:
            extractor = BrowserExtractor(self.url, self.temp_dir, self.session_id)
            pages = extractor.extract_pages()
        except Exception as e:
            logger.error(f"Error in browser automation: {str(e)}")
            download_progress[self.session_id] = {
                'current_page': 0,
                'total_pages': 0,
                'status': f"Error in browser automation: {str(e)}",
                'completed': False,
                'error': True
            }
            pages = []

        # Create PDF if we have pages
        if pages:
            logger.info(f"Captured {len(pages)} pages, creating PDF")
            pdf_generator = PdfGenerator(self.output_pdf, self.temp_dir, self.session_id)
            if pdf_generator.create_pdf(pages):
                logger.info(f"Book successfully downloaded and saved to {self.output_pdf}")
                download_progress[self.session_id] = {
                    'current_page': len(pages),
                    'total_pages': len(pages),
                    'status': f"PDF created successfully! {len(pages)} pages captured.",
                    'completed': True,
                    'error': False,
                    'file_path': self.output_pdf
                }
                self._cleanup()
                return True

        logger.error("Screenshot extraction failed")
        download_progress[self.session_id] = {
            'current_page': 0,
            'total_pages': 0,
            'status': "Screenshot extraction failed",
            'completed': False,
            'error': True
        }
        self._cleanup()
        return False

    def _cleanup(self) -> None:
        """Clean up temporary files"""
        try:
            for file in os.listdir(self.temp_dir):
                file_path = os.path.join(self.temp_dir, file)
                if os.path.isfile(file_path):
                    os.remove(file_path)
            os.rmdir(self.temp_dir)
            logger.info("Cleaned up temporary files")
        except Exception as e:
            logger.warning(f"Error cleaning up temporary files: {str(e)}")


def main():
    """Main function to run the script"""
    parser = argparse.ArgumentParser(description='Download PubHTML5 books using screenshots and convert to PDF')
    parser.add_argument('--url', type=str, default="https://online.pubhtml5.com/xaqg/ilgg/",
                        help='URL of the PubHTML5 book')
    parser.add_argument('--output', type=str, default="pubhtml5_book.pdf",
                        help='Output PDF filename')
    parser.add_argument('--verbose', action='store_true',
                        help='Enable verbose logging')

    args = parser.parse_args()

    # Set logging level
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    print(f"PubHTML5 Book Downloader (Screenshot Mode)")
    print(f"------------------------------------------")
    print(f"URL: {args.url}")
    print(f"Output: {args.output}")
    print()

    # Start download
    session_id = str(uuid.uuid4())
    downloader = PubHTML5Downloader(args.url, args.output, session_id)
    success = downloader.download()

    if success:
        print(f"\nSuccess! Book downloaded and saved to: {args.output}")
    else:
        print(f"\nFailed to download the book. Check the log file for details.")

    return 0 if success else 1


# Initialize Flask app
app = Flask(__name__)


@app.route('/')
def index():
    return '''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>PubHTML5 Book Downloader</title>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap" rel="stylesheet">
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }

            body {
                font-family: 'Poppins', sans-serif;
                background: linear-gradient(135deg, #fce4ec 0%, #f8bbd9 50%, #f48fb1 100%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }

            .container {
                background: rgba(255, 255, 255, 0.95);
                backdrop-filter: blur(10px);
                border-radius: 24px;
                padding: 40px;
                box-shadow: 0 20px 40px rgba(244, 143, 177, 0.3);
                max-width: 600px;
                width: 100%;
                text-align: center;
                border: 1px solid rgba(248, 187, 217, 0.3);
            }

            .header {
                margin-bottom: 30px;
            }

            .header h1 {
                color: #ad1457;
                font-size: 2.5rem;
                font-weight: 700;
                margin-bottom: 10px;
                text-shadow: 0 2px 4px rgba(173, 20, 87, 0.1);
            }

            .header .subtitle {
                color: #c2185b;
                font-size: 1.1rem;
                font-weight: 400;
                line-height: 1.6;
            }

            .input-group {
                margin: 30px 0;
                position: relative;
            }

            .input-wrapper {
                position: relative;
                margin-bottom: 20px;
            }

            .input-icon {
                position: absolute;
                left: 20px;
                top: 50%;
                transform: translateY(-50%);
                color: #f48fb1;
                font-size: 1.2rem;
                z-index: 2;
            }

            input[type="text"] {
                width: 100%;
                padding: 18px 20px 18px 55px;
                border: 2px solid #f8bbd9;
                border-radius: 16px;
                font-size: 16px;
                font-family: 'Poppins', sans-serif;
                background: rgba(252, 228, 236, 0.5);
                color: #ad1457;
                transition: all 0.3s ease;
                outline: none;
            }

            input[type="text"]:focus {
                border-color: #f48fb1;
                background: rgba(252, 228, 236, 0.8);
                box-shadow: 0 0 0 4px rgba(244, 143, 177, 0.2);
                transform: translateY(-2px);
            }

            input[type="text"]::placeholder {
                color: #c2185b;
                opacity: 0.7;
            }

            .download-btn {
                background: linear-gradient(135deg, #f48fb1 0%, #f06292 100%);
                color: white;
                border: none;
                padding: 18px 40px;
                border-radius: 16px;
                font-size: 18px;
                font-weight: 600;
                font-family: 'Poppins', sans-serif;
                cursor: pointer;
                transition: all 0.3s ease;
                box-shadow: 0 8px 20px rgba(244, 143, 177, 0.4);
                position: relative;
                overflow: hidden;
            }

            .download-btn:hover {
                transform: translateY(-3px);
                box-shadow: 0 12px 25px rgba(244, 143, 177, 0.5);
            }

            .download-btn:active {
                transform: translateY(-1px);
            }

            .download-btn:disabled {
                background: linear-gradient(135deg, #f8bbd9 0%, #f48fb1 100%);
                cursor: not-allowed;
                transform: none;
                box-shadow: 0 4px 10px rgba(244, 143, 177, 0.2);
            }

            .download-btn i {
                margin-right: 10px;
            }

            #loader {
                display: none;
                margin-top: 40px;
                padding: 30px;
                background: linear-gradient(135deg, rgba(252, 228, 236, 0.9) 0%, rgba(248, 187, 217, 0.9) 100%);
                border-radius: 20px;
                border: 1px solid rgba(244, 143, 177, 0.3);
            }

            .spinner {
                margin-bottom: 20px;
                font-size: 40px;
                color: #f48fb1;
                animation: spin 2s linear infinite;
            }

            @keyframes spin {
                0% { transform: rotate(0deg); }
                100% { transform: rotate(360deg); }
            }

            .loader-title {
                color: #ad1457;
                font-size: 1.5rem;
                font-weight: 600;
                margin-bottom: 10px;
            }

            .loader-subtitle {
                color: #c2185b;
                font-size: 1rem;
                margin-bottom: 25px;
            }

            .progress-info {
                margin: 25px 0;
                padding: 20px;
                background: rgba(255, 255, 255, 0.7);
                border-radius: 16px;
                border: 1px solid rgba(244, 143, 177, 0.2);
            }

            .page-counter {
                font-size: 2rem;
                font-weight: 700;
                color: #ad1457;
                margin-bottom: 10px;
                text-shadow: 0 2px 4px rgba(173, 20, 87, 0.1);
            }

            .download-status {
                margin: 15px 0;
                font-weight: 500;
                color: #c2185b;
                font-size: 1.1rem;
            }

            .note {
                background: rgba(255, 255, 255, 0.8);
                padding: 15px;
                border-radius: 12px;
                border-left: 4px solid #f48fb1;
                margin-top: 20px;
            }

            .note strong {
                color: #ad1457;
            }

            .note p {
                color: #c2185b;
                margin: 0;
            }

            .completion-message {
                display: none;
                margin-top: 30px;
                padding: 25px;
                background: linear-gradient(135deg, rgba(200, 230, 201, 0.9) 0%, rgba(165, 214, 167, 0.9) 100%);
                color: #2e7d32;
                border-radius: 20px;
                border: 1px solid rgba(76, 175, 80, 0.3);
            }

            .completion-message i {
                font-size: 2rem;
                margin-bottom: 15px;
                color: #4caf50;
            }

            .completion-message h3 {
                margin-bottom: 15px;
                font-weight: 600;
            }

            .download-link {
                margin-top: 20px;
                padding: 15px 30px;
                background: linear-gradient(135deg, #4caf50 0%, #66bb6a 100%);
                color: white;
                border: none;
                border-radius: 12px;
                cursor: pointer;
                font-size: 16px;
                font-weight: 600;
                font-family: 'Poppins', sans-serif;
                text-decoration: none;
                display: inline-block;
                transition: all 0.3s ease;
                box-shadow: 0 6px 15px rgba(76, 175, 80, 0.3);
            }

            .download-link:hover {
                transform: translateY(-2px);
                box-shadow: 0 8px 20px rgba(76, 175, 80, 0.4);
            }

            .download-link i {
                margin-right: 8px;
            }

            .error-message {
                display: none;
                margin-top: 30px;
                padding: 25px;
                background: linear-gradient(135deg, rgba(255, 235, 238, 0.9) 0%, rgba(255, 205, 210, 0.9) 100%);
                color: #c62828;
                border-radius: 20px;
                border: 1px solid rgba(244, 67, 54, 0.3);
            }

            .error-message i {
                font-size: 2rem;
                margin-bottom: 15px;
                color: #f44336;
            }

            .error-message h3 {
                margin-bottom: 10px;
                font-weight: 600;
            }

            .floating-shapes {
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                pointer-events: none;
                z-index: -1;
            }

            .shape {
                position: absolute;
                background: rgba(248, 187, 217, 0.1);
                border-radius: 50%;
                animation: float 6s ease-in-out infinite;
            }

            .shape:nth-child(1) {
                width: 80px;
                height: 80px;
                top: 20%;
                left: 10%;
                animation-delay: 0s;
            }

            .shape:nth-child(2) {
                width: 120px;
                height: 120px;
                top: 60%;
                right: 10%;
                animation-delay: 2s;
            }

            .shape:nth-child(3) {
                width: 60px;
                height: 60px;
                bottom: 20%;
                left: 20%;
                animation-delay: 4s;
            }

            @keyframes float {
                0%, 100% { transform: translateY(0px) rotate(0deg); }
                50% { transform: translateY(-20px) rotate(180deg); }
            }

            @media (max-width: 768px) {
                .container {
                    padding: 30px 20px;
                    margin: 10px;
                }

                .header h1 {
                    font-size: 2rem;
                }

                .header .subtitle {
                    font-size: 1rem;
                }

                input[type="text"] {
                    padding: 16px 18px 16px 50px;
                    font-size: 15px;
                }

                .download-btn {
                    padding: 16px 30px;
                    font-size: 16px;
                }

                .page-counter {
                    font-size: 1.5rem;
                }
            }
        </style>
    </head>
    <body>
        <div class="floating-shapes">
            <div class="shape"></div>
            <div class="shape"></div>
            <div class="shape"></div>
        </div>

        <div class="container">
            <div class="header">
                <h1><i class="fas fa-book-open"></i> PubHTML5 Downloader</h1>
                <p class="subtitle">Transform your favorite PubHTML5 books into beautiful PDFs with our advanced screenshot technology</p>
            </div>

            <div class="input-group">
                <div class="input-wrapper">
                    <i class="fas fa-link input-icon"></i>
                    <input type="text" id="url" placeholder="Paste your PubHTML5 book URL here..." required>
                </div>
                <button class="download-btn" id="downloadBtn">
                    <i class="fas fa-download"></i>Start Download
                </button>
            </div>

            <div id="loader">
                <div class="spinner">
                    <i class="fas fa-camera"></i>
                </div>
                <h3 class="loader-title">Capturing Pages...</h3>
                <p class="loader-subtitle">Our advanced technology is taking high-quality screenshots of each page</p>

                <div class="progress-info">
                    <div class="page-counter" id="pageCounter">Page 0</div>
                    <div class="download-status" id="downloadStatus">Initializing...</div>
                </div>

                <div class="note">
                    <p><strong>Please Note:</strong> Keep this window open during the download process for the best results.</p>
                </div>
            </div>

            <div class="completion-message" id="completionMessage">
                <i class="fas fa-check-circle"></i>
                <h3>Download Complete!</h3>
                <p>Your PDF has been successfully created and is ready for download.</p>
                <a href="#" id="downloadLink" class="download-link">
                    <i class="fas fa-file-pdf"></i> Download PDF
                </a>
            </div>

            <div class="error-message" id="errorMessage">
                <i class="fas fa-exclamation-triangle"></i>
                <h3>Oops! Something went wrong</h3>
                <p id="errorText">We encountered an error during the download process. Please try again.</p>
            </div>
        </div>

        <script>
            let sessionId = null;
            let progressInterval = null;

            document.getElementById('downloadBtn').onclick = function() {
                const url = document.getElementById('url').value.trim();

                if (!url) {
                    alert("Please enter a valid book URL");
                    return;
                }

                // Show loader and disable input/button
                document.getElementById('loader').style.display = 'block';
                document.getElementById('url').disabled = true;
                document.getElementById('downloadBtn').disabled = true;
                document.getElementById('completionMessage').style.display = 'none';
                document.getElementById('errorMessage').style.display = 'none';

                // Send download request
                fetch('/download', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                    },
                    body: new URLSearchParams({ url: url })
                })
                .then(response => response.json())
                .then(data => {
                    if (data.session_id) {
                        sessionId = data.session_id;
                        // Start polling for progress
                        startProgressPolling();
                    } else {
                        throw new Error(data.message || 'Unknown error');
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    document.getElementById('errorMessage').style.display = 'block';
                    document.getElementById('errorText').textContent = error.message || 'Error starting download';
                    document.getElementById('loader').style.display = 'none';
                    document.getElementById('url').disabled = false;
                    document.getElementById('downloadBtn').disabled = false;
                });
            };

            function startProgressPolling() {
                progressInterval = setInterval(() => {
                    if (!sessionId) return;

                    fetch(`/progress/${sessionId}`)
                        .then(response => response.json())
                        .then(data => {
                            updateProgress(data);

                            if (data.completed || data.error) {
                                clearInterval(progressInterval);

                                if (data.completed) {
                                    showCompletion();
                                } else if (data.error) {
                                    showError(data.status);
                                }
                            }
                        })
                        .catch(error => {
                            console.error('Progress polling error:', error);
                        });
                }, 2000); // Poll every 2 seconds
            }

            function updateProgress(data) {
                const pageCounter = document.getElementById('pageCounter');
                const downloadStatus = document.getElementById('downloadStatus');

                if (data.total_pages && data.total_pages > 0) {
                    pageCounter.textContent = `Page ${data.current_page} of ${data.total_pages}`;
                } else {
                    pageCounter.textContent = `Page ${data.current_page}`;
                }

                downloadStatus.textContent = data.status || 'Processing...';
            }

            function showCompletion() {
                document.getElementById('loader').style.display = 'none';
                document.getElementById('completionMessage').style.display = 'block';

                // Set up download link
                const downloadLink = document.getElementById('downloadLink');
                downloadLink.href = `/download-file/${sessionId}`;

                // Re-enable controls
                document.getElementById('url').disabled = false;
                document.getElementById('downloadBtn').disabled = false;
            }

            function showError(errorMessage) {
                document.getElementById('loader').style.display = 'none';
                document.getElementById('errorMessage').style.display = 'block';
                document.getElementById('errorText').textContent = errorMessage || 'Error during download';

                // Re-enable controls
                document.getElementById('url').disabled = false;
                document.getElementById('downloadBtn').disabled = false;
            }

            // Add enter key support for input
            document.getElementById('url').addEventListener('keypress', function(e) {
                if (e.key === 'Enter') {
                    document.getElementById('downloadBtn').click();
                }
            });
        </script>
    </body>
    </html>
    '''


@app.route('/download', methods=['POST'])
def download():
    url = request.form['url']
    session_id = str(uuid.uuid4())
    output_pdf = f"pubhtml5_book_{session_id}.pdf"

    # Start the download in a separate thread
    def download_task():
        downloader = PubHTML5Downloader(url, output_pdf, session_id)
        downloader.download()

    thread = Thread(target=download_task)
    thread.start()

    return jsonify({"message": "Download started", "session_id": session_id})


@app.route('/progress/<session_id>')
def get_progress(session_id):
    global download_progress

    if session_id in download_progress:
        return jsonify(download_progress[session_id])
    else:
        return jsonify({
            'current_page': 0,
            'total_pages': 0,
            'status': 'Session not found',
            'completed': False,
            'error': True
        })


@app.route('/download-file/<session_id>')
def download_file(session_id):
    global download_progress

    if session_id in download_progress and download_progress[session_id].get('completed'):
        file_path = download_progress[session_id].get('file_path')
        if file_path and os.path.exists(file_path):
            try:
                return send_file(
                    file_path,
                    as_attachment=True,
                    download_name=f"pubhtml5_book_{session_id[:8]}.pdf",
                    mimetype='application/pdf'
                )
            except Exception as e:
                logger.error(f"Error sending file: {str(e)}")
                return jsonify({"error": "File download failed"}), 500
        else:
            return jsonify({"error": "File not found"}), 404
    else:
        return jsonify({"error": "Download not completed or session not found"}), 404


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] != "run":
        # Command line mode
        main()
    else:
        # Flask web mode
        print("Starting PubHTML5 Downloader Web Interface...")
        print("Access the application at: http://localhost:5000")
        app.run(debug=True, host='0.0.0.0', port=5000)
