# Certificate Generator

This program is used to automate the process of generating and uploading certificates for GDG on Campus BME event participants or core team members.

The application currently supports:

* batch certificate generation from CSV files
* manual single-certificate generation
* previewing a certificate before exporting
* checked-in-only or all-registrants modes
* Unicode and Arabic name support
* automatic participant name capitalization
* optional Google Drive upload
* automated tests and coverage

# Features

1. Generate certificates for all eligible participants from a CSV file.
2. Generate a single certificate manually by typing a participant name.
3. Preview a certificate before generating the full batch.
4. Support accented Latin names, Turkish characters, and Arabic names.
5. Automatically capitalize participant name initials.
6. Create an output folder automatically using the event title and date.
7. Avoid filename collisions by generating unique output filenames.
8. Show progress during certificate generation.
9. Warn about duplicate names in the CSV.
10. Warn about very long names that may appear small on the certificate.
11. Upload generated certificate folders to Google Drive.

# Rules for contributing

1. Provide **informative** names for your commits, branches, and pull requests.
2. Always create a **branch** from `main` when starting work on a new issue. We do not want anyone committing directly to `main`, and this is also standard practice in enterprise development.
3. Do **NOT** merge until you get at least **2 approvals**. We are working on enforcing this rule.
4. Add or update **tests** whenever possible when introducing new functionality or changing logic.

# Git Workflow

1. Clone the repository to your machine. On the main page under the **Code** button you can find the clone options:

```bash
git clone https://github.com/gdsc-bme/certificate-generator.git
```

The SSH setup is recommended, since it is more secure and commonly used in companies.

2. Create a new branch, preferably with a name related to the issue you are working on:

```bash
git branch example-branch
git checkout example-branch
```
or
```bash
git checkout -b example-branch
```

3. When you are done, add your files for staging:

```bash
git add .
```

4. Commit your changes:

```bash
git commit -m "Your very informative commit message"
```

5. Push:

```bash
git push origin -u example-branch
```

For later changes on the same branch, you can simply call:

```bash
git push
```

6. Open a pull request on GitHub. Edit the description to reference the issue you are working on and wait for reviews.

7. As soon as your PR is approved, it can be merged.

# Running the Application

Run the GUI with:

```bash
python GUI.py
```

# How to Use

## Batch Generation from CSV

1. Open the app.
2. Select a CSV file.
3. Enter the event title.
4. Enter the event date in `DD/MM/YYYY` format.
5. Choose the eligibility mode:

   * `Checked-in only`
   * `All registrants`
6. Click **Generate Certificates**.

The app will:

* read the CSV
* filter participants according to the selected mode
* normalize participant name capitalization
* warn about duplicates and very long names
* generate certificates into a new event output folder
* show progress during generation

## Manual Single Certificate

1. Enter the event title.
2. Enter the event date.
3. Type a participant name.
4. Click **Generate Single Certificate**.

This is useful for:

* fixing a name manually
* generating a late certificate
* creating a certificate without preparing a CSV

## Preview a Certificate

1. Enter the event title.
2. Enter the event date.
3. Type a participant name.
4. Click **Preview Single Certificate**.

This creates a temporary preview and opens it so you can check the layout before generating a full batch.

# CSV Requirements

For checked-in-only generation, the CSV should contain:

* `First Name`
* `Last Name`
* `Checkin Date (UTC)`

For all-registrants mode, the app only requires:

* `First Name`
* `Last Name`

# Name Handling

The app automatically normalizes participant names by capitalizing initials.

Examples:

* `john doe` → `John Doe`
* `essa sweis` → `Essa Sweis`
* `abu-zeid` → `Abu-Zeid`
* `o'connor` → `O'Connor`

This is applied to:

* CSV-based generation
* manual single-certificate generation
* certificate preview

# Arabic and Unicode Name Support

The generator supports:

* accented Latin names
* Turkish characters
* Arabic names

Arabic names are handled using:

* `arabic-reshaper`
* `python-bidi`

The current setup uses Windows system Arial fonts for Arabic rendering.

# Google Drive Upload

The app can upload generated certificate folders to Google Drive.

Required local auth files:

* `client_secret.json`
* `token.json`

These files must **not** be committed to the repository.

## Creating a Virtual Environment

A virtual environment (`venv` / `virtualenv`) allows you to manage different package installations for different projects.

Make sure you have the latest version of pip by running:

**Unix/macOS:**

```sh
python3 -m pip install --user --upgrade pip
python3 -m pip --version
```

**Windows:**

```sh
py -m pip install --upgrade pip
py -m pip --version
```

You can then run the following command to create the venv:

**Unix/macOS:**

```sh
python3 -m venv .venv
```

**Windows:**

```sh
py -m venv .venv
```

Activate it with the following:

**Unix/macOS:**

```sh
source .venv/bin/activate
```

**Windows:**

```sh
.\.venv\Scripts\activate
```

Finally, you can leave it with the following:

```sh
deactivate
```

# Installing Packages

The `requirements.txt` file contains the packages deemed necessary at the time of writing. However, this is subject to change.

```bash
pip install -r requirements.txt
pip install <package>
```

IMPORTANT: Each time you install a package locally, do not forget to add it to `requirements.txt`, preferably with the version as well.

# Freezing Packages

When you install packages locally and want to export them and their installed versions, you can use the following:

```bash
python -m pip freeze
```

This outputs package specifiers like the following:

```text
cachetools==5.3.3
pandas==2.2.1
pillow==10.2.0
pytest==9.0.2
python-bidi==0.6.7
arabic-reshaper==3.0.0
```

This can be used to amend the `requirements.txt`.

# Testing

The project includes automated tests for:

* date validation
* CSV parsing and eligibility filtering
* filename safety
* duplicate and long-name detection
* Unicode and Arabic handling
* certificate file generation
* GUI action logic

Run all tests:

```bash
pytest -v
```

Run tests with coverage:

```bash
pytest --cov=GUI --cov=certificateEditor --cov-report=term-missing
```

# Building the Windows Executable

To build the app as a Windows `.exe`, install PyInstaller:

```bash
pip install pyinstaller
```

Then build:

```bash
pyinstaller --onefile --windowed --name "GDG Certificate Generator" ^
  --add-data "template_certificate_no_line.jpg:." ^
  --add-data "Open_Sans/static/OpenSans-Bold.ttf:Open_Sans/static" ^
  --add-data "Open_Sans/static/OpenSans-Regular.ttf:Open_Sans/static" ^
  GUI.py
```

The built executable will appear in:

```text
dist/
```

## Feedback

If you have any feedback, please reach out to us on our [Discord](https://discord.gg/VAwkGyHmw9) and let us know!
