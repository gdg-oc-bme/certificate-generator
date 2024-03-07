# Certificate Generator
This program is used to help automate the tedious process of manually editing and uploading certificates to GDSC event participants or core team members.

# Rules for contributing
1. Provide **informative** names for your commits/branches/PRs.
2. Always create a **branch** from main when starting to work on a new issue! It is an important step because we do not want anyone to commit directly to main, and this is how it is done in the enterprise too.
3. Do NOT merge until you get at least **2 approvals**! We are working on enforcing this rule.
4. If possible, write a **unit test** for your piece of functionality. This is necessary to ensure that later changes will not break the original functionality.

# Git 101
1. Clone the repository to your machine. On the main page under the "Code" button you can find the options:
```
git clone https://github.com/gdsc-bme/certificate-generator.git
```
The SSH setup is recommended as it is more secure and the companies mostly use it as well.

2. Create a new branch, preferrably give it a name related to the issue you are working on. 
```
git branch example-branch
git checkout example-branch
```
3. When done, add your files for staging
```
git add .
```
4. Commit your changes
```
git commit -m "Your very informative commit message"
```
5. Push
```
git push origin -u example-branch
```
For all your next changes you can simply call ```git push```.

6. Open a pull request on the GitHub UI. Edit the description to reference the issue you are working on. Wait for reviews from others.

7. As soon as your PR is approved, it can be merged.

# Installing Packages
The requirements.txt file contains the packages deemed to be necessary at the time of writing. However, this is subject to change.
```
pip install -r requirements.txt # Each time there is a new package
pip install <package> # Each time you want to install a package
```
IMPORTANT: Each time you install a package locally, do not forget to add it to the requirements.txt (preferably with the version as well).
#Freezing Packages
When you install packages locally and want to export them and their installed versions, you can use the following:
`python3 -m pip freeze`
This outputs package specifiers like the following:
```
cachetools==2.0.1
certifi==2017.7.27.1
chardet==3.0.4
google-auth==1.1.1
idna==2.6
pyasn1==0.3.6
pyasn1-modules==0.1.4
requests==2.18.4
rsa==3.4.2
six==1.11.0
urllib3==1.22
```
This can be used to amend the `requirements.txt`
## Creating a Virtual Environment

A virtual environment (venv/virtualenv) allows you to manage different package installations for different projects.

Make sure you have the latest version of pip by running:


**Unix/macOS:**
```sh
python3 -m pip install --user --upgrade pip
python3 -m pip install --user --upgrade pip
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
source /.venv/bin/activate
```
**Windows:**
```sh
.\.venv\Scripts\activate
```

Finally, you can leave it with the following:

```sh
deactivate
```

## Feedback

If you have any feedback, please reach out to us on our [Discord](https://discord.gg/VAwkGyHmw9) and let us know!
