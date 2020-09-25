import json
import re
import sys
import requests
import googlemaps
import os
from datetime import datetime
from urllib import parse

from fake_useragent import UserAgent
from requests_html import HTMLSession

# Add your API key here
gmaps = googlemaps.Client(key=os.environ['GOOGLE_API_KEY'])

session = HTMLSession()
ua = UserAgent()


def _clean(text):
    text = text.replace('\xa0', ' ').replace(',-', '').replace(' m²', '')
    try:
        text = int(re.sub(r'kr$', '', text).replace(' ', ''))
    except ValueError:
        pass

    return text


def _find_travel_times(address):
    data = {}
    travel_times_transit = {}
    travel_times_driving = {}
    # destinations = ['Oslo Sentralstasjon', 'Accenture, Fornebu', 'Den Franske Skolen',
    #                 'Rosenvilde VGS']
    destinations = os.environ['DESTINATIONS']

    now = datetime.now()
    # latest_arrival_time = datetime.fromisoformat('2020-09-16 08:00+02:00')
    latest_arrival_time = datetime.fromisoformat(os.environ['LATEST_ARRIVAL_TIME'])

    try:
        for dest in destinations:
            directions_result_transit = gmaps.directions(origin=address,
                                                 destination=dest,
                                                 mode="transit",
                                                 arrival_time=latest_arrival_time, alternatives=True)

            travel_times_transit[dest] = {}
            travel_times_transit[dest]['Reisetid'] = directions_result_transit[0]['legs'][0]['duration']['text']
            travel_times_transit[dest]['Avstand'] = directions_result_transit[0]['legs'][0]['distance']['text']

            departure_time = directions_result_transit[0]['legs'][0]['departure_time']['value']
            travel_times_transit[dest]['Avgang'] = datetime.fromtimestamp(departure_time).isoformat()
            arrival_time = directions_result_transit[0]['legs'][0]['arrival_time']['value']
            travel_times_transit[dest]['Ankomst'] = datetime.fromtimestamp(arrival_time).isoformat()

            directions_result_driving = gmaps.directions(origin=address,
                                                 destination=dest,
                                                 mode="driving",
                                                 arrival_time=latest_arrival_time)

            travel_times_driving[dest] = {}
            travel_times_driving[dest]['Reisetid'] = directions_result_driving[0]['legs'][0]['duration']['text']
            travel_times_driving[dest]['Avstand'] = directions_result_driving[0]['legs'][0]['distance']['text']

            data[dest] = {}
            data[dest]['Kollektivt'] = "{:.0f} min".format(directions_result_transit[0]['legs'][0]['duration']['value'] / 60)
            data[dest]['Bil'] = "{:.0f} min".format(directions_result_driving[0]['legs'][0]['duration']['value'] / 60)

    except:
        print("Error in getting travel time")
        return data

    data['Reisetider (kollektivt)'] = travel_times_transit
    print('Successfully added TRANSIT travel times for ' + address)

    data['Reisetider (bil)'] = travel_times_driving
    print('Successfully added DRIVING travel times for ' + address)

    return data


def _parse_data_lists(html):
    data = {}
    days = ['Man.', 'Tir.', 'Ons.', 'Tor.', 'Fre', 'Lør.', 'Søn.']
    skip_keys = ['Mobil', 'Fax', '', ] + days  # Unhandled data list labels

    data_lists = html.find('dl')
    for el in data_lists:
        values_list = iter(el.find('dt, dd'))
        for a in values_list:
            _key = a.text
            a = next(values_list)
            if _key in skip_keys:
                continue
            data[_key] = _clean(a.text)

    return data


def _scrape_viewings(html):
    # Find links to ICAL downloads
    viewings = set()
    calendar_url = [el.attrs["href"] for el in html.find('a[href*=".ics"]')]
    for url in calendar_url:
        query_params = dict(parse.parse_qsl(parse.urlsplit(url).query))
        dt = datetime.strptime(query_params['iCalendarFrom'][:-1], '%Y%m%dT%H%M%S')
        if dt:
            viewings.add(dt.isoformat())
    return list(viewings)


def _calc_price(ad_data):
    debt = ad_data.get('Fellesgjeld', 0)
    cost = ad_data.get('Omkostninger', 0)
    return ad_data['Totalpris'] - debt - cost


def _scrape_about_nabolaget(finnkode):
    data = {}
    url = 'https://www.finn.no/realestate/neighborhood-api.json?finnkode={code}'.format(code=finnkode)

    r = requests.get(url)
    r.raise_for_status()

    data_json = r.json()
    walking_distances = data_json['cards'][0]['data']['pois']

    data['Gåavstander'] = {}

    for wd in walking_distances:
        if (wd['distanceType'] == 'walk'):
            data['Gåavstander'][wd['name']] = wd['distance']

    return data


def scrape_ad(finnkode):
    url = 'https://www.finn.no/realestate/homes/ad.html?finnkode={code}'.format(code=finnkode)
    r = session.get(url, headers={'user-agent': ua.random})
    r.encoding = "utf-8"

    r.raise_for_status()

    html = r.html

    postal_address_element = html.find('h1 + p', first=True)
    if not postal_address_element:
        return

    ad_data = {
        'Postadresse': postal_address_element.text,
        'URL': url,
    }

    CSS_SELECTOR_AREA = 'body > main > div > div.grid > div.grid__unit.u-r-size2of3 > div > section:nth-child(3) > span'
    area_element = html.find(CSS_SELECTOR_AREA, first=True)
    if area_element:
        ad_data['Område'] = area_element.text

    ad_data.update(_scrape_about_nabolaget(finnkode))
    ad_data.update(_find_travel_times(ad_data['Postadresse']))

    viewings = _scrape_viewings(html)
    if viewings:
        ad_data['Visninger'] = viewings
        ad_data.update({'Visning {}'.format(i): v for i, v in enumerate(viewings, start=1)})

    ad_data.update(_parse_data_lists(html))

    if 'Totalpris' in ad_data:
        ad_data['Prisantydning'] = _calc_price(ad_data)

    return ad_data


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print('Invalid number of arguments.\n\nUsage:\n$ python finn.py FINNKODE')
        exit(1)

    ad = scrape_ad(sys.argv[1])
    print(json.dumps(ad, indent=2, ensure_ascii=False))
