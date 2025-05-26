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
            max_pages = 700  # Assume a maximum of 700 pages

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
            .progress-info {
                margin: 20px 0;
                padding: 15px;
                background-color: #e9ecef;
                border-radius: 5px;
            }
            .page-counter {
                font-size: 24px;
                font-weight: bold;
                color: #28a745;
                margin-bottom: 10px;
            }
            .download-status {
                margin: 15px 0;
                font-weight: bold;
                color: #333;
            }
            .completion-message {
                display: none;
                margin-top: 20px;
                padding: 15px;
                background-color: #d4edda;
                color: #155724;
                border-radius: 4px;
            }
            .download-button {
                margin-top: 15px;
                padding: 12px 25px;
                background-color: #007bff;
                color: white;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-size: 16px;
                text-decoration: none;
                display: inline-block;
            }
            .download-button:hover {
                background-color: #0056b3;
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
            <h1>PubHTML5 Book Downloader</h1>
            <p>Enter the URL of the PubHTML5 book you want to download as PDF using screenshots</p>

            <div>
                <input type="text" id="url" placeholder="Enter the book URL (e.g., https://online.pubhtml5.com/)" required>
                <button id="downloadBtn">Download</button>
            </div>

            <div id="loader">
                <div class="spinner">
                    <i class="fas fa-spinner fa-spin"></i>
                </div>
                <h3>Taking Screenshots...</h3>
                <p>Please wait while we capture each page.</p>

                <div class="progress-info">
                    <div class="page-counter" id="pageCounter">Page 0</div>
                    <div class="download-status" id="downloadStatus">Initializing...</div>
                </div>

                <p><b>Note:</b> Please do not close this window during the download process.</p>
            </div>

            <div class="completion-message" id="completionMessage">
                <i class="fas fa-check-circle"></i> Download complete! Your PDF has been created.
                <br><br>
                <a href="#" id="downloadLink" class="download-button">
                    <i class="fas fa-download"></i> Download PDF
                </a>
            </div>

            <div class="error-message" id="errorMessage">
                <i class="fas fa-exclamation-circle"></i> <span id="errorText">Error during download. Please try again later.</span>
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
