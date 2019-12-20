#!/usr/bin/python3

import os
import re
import sys
import yaml
import jinja2
import jenkins
import argparse
import bugzilla
import datetime
from jira import JIRA

from smtplib import SMTP
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

os.environ['PYTHONHTTPSVERIFY'] = '0'

# function definitions
def get_bugzilla(job_name):

	# get all bug ids for job from blocker file
	try:
		with open(blockers, 'r') as file:
			bug_file = yaml.safe_load(file)
			bug_ids = bug_file[job_name]['bz']
	except Exception as e:
		print("Error loading blocker configuration data (Bugzilla): ", e)
		bugs = [{'bug_name': "Could not find relevant bug", 'bug_url': None}]
	else:

		# initialize bug list
		bugs = []

		# iterate through bug ids from blocker file
		for bug_id in bug_ids:

			# 0 should be default in YAML file (i.e. no bugs recorded)
			# if there is a 0 entry then that should be the only "bug", so break
			if bug_id == 0:
				bugs = [{'bug_name': 'No bug on file', 'bug_url': None}]
				break

			# otherwise record real bug in overall list
			all_bugs.append(bug_id)

			# get bug info from bugzilla API
			try:

				# hotfix: API call does not work if '/' present at end of URL string
				parsed_bz_url = config['bugzilla_url'].rstrip('/')

				bz_api = bugzilla.Bugzilla(parsed_bz_url)
				bug = bz_api.getbug(bug_id)
				bug_name = bug.summary
			except Exception as e:
				print("Bugzilla API Call Error: ", e)
				bug_name = "{}: Bugzilla API Call Error".format(bug_id)
			finally:
				bug_url = config['bugzilla_url'] + "/show_bug.cgi?id=" + str(bug_id)
				bugs.append(
					{
						'bug_name': bug_name, 
						'bug_url': bug_url
					}
				)

	return bugs

def get_jira(job_name):

	# get all tickets for job from YAML file
	try:
		with open(blockers, 'r') as file:
			jira_file = yaml.safe_load(file)
			ticket_ids = jira_file[job_name]['jira']
	except Exception as e:
		print("Error loading blocker configuration data (Jira): ", e)
		tickets = [{'ticket_name': "Could not find relevant ticket", 'ticket_url': None}]
	else:

		# initialize ticket list
		tickets = []

		# iterate through ticket ids from blocker file
		for ticket_id in ticket_ids:

			# 0 should be default in YAML file (i.e. no tickers recorded)
			# if there is a 0 entry then that should be the only "ticket", so break
			if ticket_id == 0:
				tickets = [{'ticket_name': 'No ticket on file', 'ticket_url': None}]
				break

			# otherwise record real ticket in overall list
			all_tickets.append(ticket_id)

			# get ticket info from jira API
			try:
				options = {
					"server": config['jira_url'],
					"verify": config['certificate']
				}
				jira = JIRA(options)
				issue = jira.issue(ticket_id)
				ticket_name = issue.fields.summary
			except Exception as e:
				print("Jira API Call Error: ", e)
				ticket_name = "{}: Jira API Call Error".format(ticket_id)
			finally:
				ticket_url = config['jira_url'] + "/browse/" + str(ticket_id)
				tickets.append(
					{
						'ticket_name': ticket_name, 
						'ticket_url': ticket_url
					}
				)

	return tickets

def get_osp_version(job_name):
	version = re.search(r'\d+', job_name).group()
	return version

def percent(part, whole):
	return round(100 * float(part)/float(whole), 1)

# main script execution
if __name__ == '__main__':

	# argument parsing
	parser = argparse.ArgumentParser(description='An automated report generator for Jenkins CI')
	parser.add_argument("--config", default="config.yaml", type=str, help="Configuration YAML file to use")
	parser.add_argument("--blockers", default="blockers.yaml", type=str, help="Blockers YAML file to use")
	args = parser.parse_args()
	conf = args.config
	blockers = args.blockers

	# load configuration data
	try:
		with open(conf, 'r') as file:
			config = yaml.safe_load(file)
	except Exception as e:
		print("Error loading configuration data: ", e)
		sys.exit()

	# connect to jenkins server
	try:
		server = jenkins.Jenkins(config['jenkins_url'], username=config['username'], password=config['api_token'])
		user = server.get_whoami()
		version = server.get_version()
	except Exception as e:
		print("Error connecting to Jenkins server: ", e)
		sys.exit()

	# generate report header
	user_properties = user['property']
	user_email_address = [prop['address'] for prop in user_properties if prop['_class'] == 'hudson.tasks.Mailer$UserProperty'][0]
	header = "Report generated by {} from Jenkins {} on {}".format(user_email_address, version, datetime.datetime.now())

	# fetch relevant jobs from server
	jobs = server.get_jobs()
	jobs = [job for job in jobs if config['job_search_field'] in job['name']]

	# initialize python variables
	num_jobs = len(jobs)
	num_success = 0
	num_unstable = 0
	num_failure = 0
	num_error = 0
	all_bugs = []
	all_tickets = []
	rows = []

	# collect info from all relevant jobs
	for job in jobs[::-1]:
		job_name = job['name']
		osp_version = get_osp_version(job_name)
		
		try:
			job_info = server.get_job_info(job_name)
			job_url = job_info['url']
			lcb_num = job_info['lastCompletedBuild']['number']
			lcb_url = job_info['lastCompletedBuild']['url']
			build_info = server.get_build_info(job_name, lcb_num)
			lcb_result = build_info['result']
		except Exception as e:
			print("Jenkins API call error: ", e)
			continue

		if lcb_result == "SUCCESS":
			num_success += 1
			bugs = [{'bug_name': 'N/A', 'bug_url': None}]
			tickets = [{'ticket_name': 'N/A', 'ticket_url': None}]
		elif lcb_result == "UNSTABLE":
			num_unstable += 1
			bugs = get_bugzilla(job_name)
			tickets = get_jira(job_name)
		elif lcb_result == "FAILURE":
			num_failure += 1
			bugs = get_bugzilla(job_name)
			tickets = get_jira(job_name)
		else:
			lcb_result = "ERROR"
			num_error += 1
			bugs = [{'bug_name': 'N/A', 'bug_url': None}]
			tickets = [{'ticket_name': 'N/A', 'ticket_url': None}]

		row = {'osp_version': osp_version,
				'job_name': job_name,
				'job_url': job_url,
				'lcb_num': lcb_num,
				'lcb_url': lcb_url,
				'lcb_result': lcb_result,
				'bugs': bugs,
				'tickets': tickets
		}

		rows.append(row)

	# calculate summary
	summary = {}
	summary['total_success'] = "Total SUCCESS:  {}/{} = {}%".format(num_success, num_jobs, percent(num_success, num_jobs))
	summary['total_unstable'] = "Total UNSTABLE: {}/{} = {}%".format(num_unstable, num_jobs, percent(num_unstable, num_jobs))
	summary['total_failure'] = "Total FAILURE:  {}/{} = {}%".format(num_failure, num_jobs, percent(num_failure, num_jobs))
	
	if len(all_bugs) == 0: 
		summary['total_bugs'] = "Blocker Bugs: 0 total"
	else:
		unique_bugs = set(all_bugs)
		summary['total_bugs'] = "Blocker Bugs: {} total, {} unique".format(len(all_bugs), len(unique_bugs))
		
	if len(all_tickets) == 0:
		summary['total_tickets'] = "Blocker Tickets: 0 total"
	else:
		unique_tickets = set(all_tickets)
		summary['total_tickets'] = "Blocker Tickets: {} total, {} unique".format(len(all_tickets), len(unique_tickets))
		
	if num_error > 0:
		summary['total_error'] = "Total ERROR:  {}/{} = {}%".format(num_error, num_jobs, percent(num_error, num_jobs))
	else:
		summary['total_error'] = False

	# initialize jinja2 vars
	loader = jinja2.FileSystemLoader('./template.html')
	env = jinja2.Environment(loader=loader)
	template = env.get_template('')

	# generate HTML report
	htmlcode = template.render(
		header=header,
		rows=rows,
		summary=summary
	)

	# construct email
	msg = MIMEMultipart()
	msg['From'] = user_email_address
	msg['Subject'] = config['email_subject']
	msg['To'] = config['email_to']
	msg.attach(MIMEText(htmlcode, 'html'))

	# create SMTP session - if jeeves is unable to do so an HTML file will be generated
	try:
		with SMTP(config['smtp_host']) as smtp:

			# start TLS for security
			smtp.starttls()

			# use ehlo or helo if needed
			smtp.ehlo_or_helo_if_needed()

			# send email
			smtp.sendmail(msg["From"], msg["To"], msg.as_string())
	except:
		with open("report.html", "w") as file:
			print("Error sending email report - HTML file generated")
			file.write(htmlcode)
