import uuid
import pytest
from selenium import webdriver
from selenium.webdriver.common.by import By
import time

import skyportal
from skyportal.tests import api


def test_source_list(driver, user, public_source, private_source):
    driver.get(f"/become_user/{user.id}")  # TODO decorator/context manager?
    assert 'localhost' in driver.current_url
    driver.get('/')

    simbad_class = public_source.altdata['simbad']['class']
    driver.wait_for_xpath("//div[contains(@title,'connected')]")
    driver.wait_for_xpath('//h2[contains(text(), "Sources")]')
    driver.wait_for_xpath(f'//a[text()="{public_source.id}"]')
    driver.wait_for_xpath(f'//td[text()="{simbad_class}"]')
    driver.wait_for_xpath_to_disappear(f'//a[text()="{private_source.id}"]')
    el = driver.wait_for_xpath('//button[contains(.,"Next Page")]')
    assert not el.is_enabled()
    el = driver.wait_for_xpath('//button[contains(.,"Previous Page")]')
    assert not el.is_enabled()


def test_source_filtering_and_pagination(driver, user, public_group, upload_data_token):
    obj_id = str(uuid.uuid4())
    for i in range(205):
        status, data = api('POST', 'sources',
                           data={'id': f'{obj_id}_{i}',
                                 'ra': 234.22,
                                 'dec': -22.33,
                                 'redshift': 3,
                                 'altdata': {'simbad': {'class': 'RRLyr'}},
                                 'transient': False,
                                 'ra_dis': 2.3,
                                 'group_ids': [public_group.id]},
                           token=upload_data_token)
        assert status == 200
        assert data['data']['id'] == f'{obj_id}_{i}'

    driver.get(f"/become_user/{user.id}")  # TODO decorator/context manager?
    assert 'localhost' in driver.current_url
    driver.get('/')

    driver.wait_for_xpath("//div[contains(@title,'connected')]")
    driver.wait_for_xpath('//h2[contains(text(), "Sources")]')
    driver.wait_for_xpath('//td[text()="RRLyr"]')
    # Pagination
    next_button = driver.wait_for_xpath('//button[contains(.,"Next Page")]')
    prev_button = driver.wait_for_xpath('//button[contains(.,"Previous Page")]')
    assert next_button.is_enabled()
    assert not prev_button.is_enabled()
    driver.scroll_to_element_and_click(next_button)
    time.sleep(0.5)
    assert prev_button.is_enabled()
    next_button.click()
    time.sleep(0.5)
    assert not next_button.is_enabled()
    prev_button.click()
    time.sleep(0.5)
    assert next_button.is_enabled()
    prev_button.click()
    time.sleep(0.5)
    assert not prev_button.is_enabled()
    # Jump to page
    jump_to_page_input = driver.wait_for_xpath("//input[@name='jumpToPageInputField']")
    jump_to_page_input.clear()
    jump_to_page_input.send_keys('3')
    jump_to_page_button = driver.wait_for_xpath('//button[contains(.,"Jump to page:")]')
    jump_to_page_button.click()
    time.sleep(0.5)
    #driver.wait_for_xpath('//div[contains(text(), "Displaying 1-100")]')
    assert prev_button.is_enabled()
    assert not next_button.is_enabled()
    jump_to_page_input.clear()
    jump_to_page_input.send_keys('1')
    jump_to_page_button.click()
    time.sleep(0.5)
    assert next_button.is_enabled()
    assert not prev_button.is_enabled()
    # Source filtering
    assert next_button.is_enabled()
    obj_id = driver.wait_for_xpath("//input[@name='sourceID']")
    obj_id.clear()
    obj_id.send_keys('aaaa')
    submit = driver.wait_for_xpath("//button[contains(.,'Submit')]")
    driver.scroll_to_element_and_click(submit)
    time.sleep(1)
    assert not next_button.is_enabled()


def test_jump_to_page_invalid_values(driver):
    driver.get('/')
    jump_to_page_input = driver.wait_for_xpath("//input[@name='jumpToPageInputField']")
    jump_to_page_input.clear()
    jump_to_page_input.send_keys('abc')
    jump_to_page_button = driver.wait_for_xpath('//button[contains(.,"Jump to page:")]')
    driver.scroll_to_element_and_click(jump_to_page_button)
    driver.wait_for_xpath('//div[contains(.,"Invalid page number value")]')


def test_skyportal_version_displayed(driver):
    driver.get('/')
    driver.wait_for_xpath(f"//div[contains(.,'SkyPortal v{skyportal.__version__}')]")
