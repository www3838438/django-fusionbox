from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

# `setlocale` is not threadsafe
import locale
locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')

import re
import warnings
import calendar

inflect = None
try:
    import inflect
    inflect = inflect.engine()
except ImportError:
    pass

from django import template
from django.conf import settings

from BeautifulSoup import BeautifulSoup
from django.utils import simplejson
from django.utils.safestring import mark_safe
from django.contrib.humanize.templatetags.humanize import intcomma
from django.core.exceptions import ImproperlyConfigured

register = template.Library()


def addclass(elem, cls):
    elem['class'] = elem.get('class', '')
    elem['class'] += ' ' + cls if elem['class'] else cls


def is_here(current, url):
    """
    Determine if current is 'underneath' url.
    """
    if url == '/':
        return current == '/'
    if current.startswith(url):
        return True
    else:
        return False


class HighlighterBase(template.Node):
    """
    Base class for templatetags that highlight specific DOM elements.

    Child classes must implement a ``elems_to_highlight`` method, which should
    return a iterable of elements to modify.  Optionally, they can override the
    ``highlight`` method, which by default simply adds a class to the DOM
    elements.

    Each templatetag accepts an optional ``self.highlight_class`` parameter and
    all other options are stored in ``self.options``.  This behavior can be
    overriden by implementing the ``parse_options`` method.
    """
    def __init__(self, parser, token):
        self.parse_options(token.split_contents())

        self.nodelist = parser.parse(('endhighlight',))
        parser.delete_first_token()

    def parse_options(self, tokens):
        self.options = tokens[1:]
        try:
            self.highlight_class = self.options.pop(0).replace('"', '')
        except IndexError:
            self.highlight_class = None

    def elems_to_highlight(self, soup, context):
        """
        Returns an iterable of all DOM elements to be highlighted.

        Accepts a BeautifulSoup object of the HTML
        """
        raise NotImplemented

    def build_soup(self, context):
        content = self.nodelist.render(context)
        soup = BeautifulSoup(content)

        return soup

    def highlight(self, elem):
        addclass(elem, self.highlight_class)

    def render(self, context):
        soup = self.build_soup(context)

        try:
            for elem in self.elems_to_highlight(soup, context):
                self.highlight(elem)
        except ImproperlyConfigured as e:
            if settings.DEBUG:
                raise
            else:
                # This is because the django 500 error view does not use a
                # request context. We still need to be able to render some kind
                # of error page, so we'll just return our contents unchanged.
                warnings.warn(e.args[0])

        return str(soup)


class HighlightHereNode(HighlighterBase):
    """
    Filter the subnode's output to add a 'here' class to every anchor where
    appropriate, based on startswith matching.

    Given::

        {% highlight_here %}
            <a href="/" class="home">/</a>
            <a href="/blog/">blog</a>
        {% endhighlight %}

    If request.url is ``/``, the output is::

        <a href="/" class="home here">/</a>
        <a href="/blog/">blog</a>

    On ``/blog/``, it is::

        <a href="/" class="home">/</a>
        <a href="/blog/" class="here">blog</a>

    """
    def __init__(self, parser, token):
        super(HighlightHereNode, self).__init__(parser, token)

        self.highlight_class = self.highlight_class or 'here'

    def elems_to_highlight(self, soup, context):
        try:
            path = template.Variable(self.options[0]).resolve(context)
        except template.VariableDoesNotExist:
            path = self.options[0]
        except IndexError:
            if 'request' in context:
                path = context['request'].path
            else:
                raise ImproperlyConfigured(
                        "The request was not available in the context, please ensure that the request is made available in the context.")

        return (anchor for anchor in soup.findAll('a', {'href': True}) if is_here(path, anchor['href']))


register.tag("highlight_here", HighlightHereNode)


class HighlightHereParentNode(HighlightHereNode):
    """
    Adds a here class to the parent of the anchor link.  Useful for nested navs
    where highlight style might bubble upwards.

    Given::

        {% highlight_here_parent %}
         <ul>
            <li id="navHome" class="parent_home">
                <a href="/" class="home">/</a>
            </li>
            <li id="navblog" class="">
                <a href="/blog/">blog</a>
            </li>
         </ul>
        {% endhighlight %}

    If request.url is ``/``, the output is::

        <ul>
            <li id="navHome" class="parent_home here">
                <a href="/" class="home">/</a>
            </li>
            <li>
                <a href="/blog/">blog</a>
            </li>
        <ul>

    """
    def elems_to_highlight(self, soup, href):
        for anchor in super(HighlightHereParentNode, self).elems_to_highlight(soup, href):
            yield anchor.parent

register.tag("highlight_here_parent", HighlightHereParentNode)


@register.filter_function
def attr(obj, arg1):
    att, value = arg1.split("=")
    obj.field.widget.attrs[att] = value
    return obj


def more_json(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if hasattr(obj, 'to_json'):
        return obj.to_json()
    raise TypeError("%r is not JSON serializable" % (obj,))


@register.filter
def json(a):
    return mark_safe(simplejson.dumps(a, default=more_json))
json.is_safe = True


@register.filter
def currency(dollars):
    """
    Takes a number and prints it as a price, with a dollar sign and two decimal
    places.

    Taken from:
    http://stackoverflow.com/a/2180209/1013960
    """
    dollars = float(dollars)

    return "$%s%s" % (intcomma(int(dollars)), ("%0.2f" % dollars)[-3:])


if hasattr(settings, 'FORMAT_TAG_ERROR_VALUE'):
    FORMAT_TAG_ERROR_VALUE = settings.FORMAT_TAG_ERROR_VALUE
else:
    FORMAT_TAG_ERROR_VALUE = 'error'


@register.filter
def us_dollars(value):
    """
    Returns the value formatted as whole US dollars.

    Example:
        if value = -20000
        {{ value|us_dollars }} => -$20,000
    """
    # Try to convert to Decimal
    try:
        value = Decimal(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    except InvalidOperation as e:
        if re.search('Invalid literal for Decimal', e[0]):
            return FORMAT_TAG_ERROR_VALUE
        else:
            raise e
    except TypeError:
        return FORMAT_TAG_ERROR_VALUE
    # Format as currency value
    return locale.currency(value, grouping=True)[:-3]


@register.filter
def us_cents(value, places=1):
    """
    Returns the value formatted as US cents.  May specify decimal places for
    fractional cents.

    Example:
        where c means cents symbol:

        if value = -20.125
        {{ value|us_cents }} => -20.1 \u00a2

        if value = 0.082
        {{ value|us_cents:3 }} => 0.082 \u00a2
    """
    # Try to convert to float
    try:
        value = float(value)
    except ValueError as e:
        if re.search('invalid literal for float', e[0]):
            return FORMAT_TAG_ERROR_VALUE
        else:
            raise e
    except TypeError:
        return FORMAT_TAG_ERROR_VALUE
    # Require places >= 0
    places = max(0, places)
    # Get negative sign
    sign = u'-' if value < 0 else u''
    # Get formatted value
    formatted = unicode(locale.format(
        '%0.' + str(places) + 'f',
        abs(value),
        grouping=True,
        ))
    # Return value with sign and cents symbol
    return sign + formatted + u'\u00a2'


@register.filter
def us_dollars_and_cents(value, cent_places=2):
    """
    Returns the value formatted as US dollars with cents.  May optionally
    include extra digits for fractional cents.  This is common when displaying
    utility rates, for instance.

    Example:
        if value = -20000.125
        {{ value|us_dollars_and_cents }} => -$20,000.13

        if value = 0.082  (8.2 cents)
        {{ value|us_dollars_and_cents:3 }} => $0.082
    """
    # Try to convert to Decimal
    try:
        value = Decimal(value).quantize(Decimal('1.' + '0' * cent_places), rounding=ROUND_HALF_UP)
    except InvalidOperation as e:
        if re.search('Invalid literal for Decimal', e[0]):
            return FORMAT_TAG_ERROR_VALUE
        else:
            raise e
    except TypeError:
        return FORMAT_TAG_ERROR_VALUE
    # Require cent_places >= 2
    if cent_places < 2:
        cent_places = 2
    # Get extra cent places if needed
    if cent_places > 2:
        extra_places = cent_places - 2
        extra_places = str(value)[-extra_places:]
    else:
        extra_places = ''
    # Format as currency value
    return locale.currency(value, grouping=True) + extra_places


@register.filter
def add_commas(value, round=None):
    """
    Add commas to a numeric value, while rounding it to a specific number of
    places.  Humanize's intcomma is not adequate since it does not allow
    formatting of real numbers.

    Example:
        if value = 20000
        {{ value|add_commas }} => 20,000
        {{ value|add_commas:3 }} => 20,000.000

        if value = 1234.5678
        {{ value|add_commas:2 }} => 1,234.57
    """
    # Decimals honor locale settings correctly
    try:
        value = Decimal(str(value))
    except InvalidOperation as e:
        if re.search('Invalid literal for Decimal', e[0]):
            return FORMAT_TAG_ERROR_VALUE
        else:
            raise e
    except TypeError:
        return FORMAT_TAG_ERROR_VALUE
    # Round the value if necessary
    if round != None:
        if round > 0:
            format = Decimal('1.' + round * '0')
        else:
            format = Decimal('1')
        value = value.quantize(format, rounding=ROUND_HALF_UP)
    # Locale settings properly format Decimals with commas
    # Super gross, but it works for both 2.6 and 2.7.
    return locale.format("%." + str(round) + "f", value, grouping=True)


@register.filter
def naturalduration(time, show_minutes=None):
    """
    Displays a time delta in a form that is more human-readable.  Accepts a
    datetime.timedelta object.  Microseconds in timedelta objects are ignored.
    The `show_minutes` argument specifies whether or not to include the number
    of minutes in the display.  If it evaluates to false, minutes are not
    included and are rounded into the number of hours.

    Example:
        if time = datetime.timedelta(2, 7280, 142535)
        {{ time|naturalduration }} => 2 days, 2 hours
        {{ time|naturalduration:"minutes" }} => 2 days, 2 hours, 1 minute
    """
    days = time.days
    hours = int(time.seconds / 3600) if show_minutes else int(round(time.seconds / 3600.0))
    minutes = int((time.seconds % 3600) / 60) if show_minutes else 0

    display = []
    if days:
        display.append('{0} days'.format(days))
    if hours:
        display.append('{0} hours'.format(hours))
    if minutes:
        display.append('{0} minutes'.format(minutes))

    return ', '.join(display)


@register.filter
def pluralize_with(count, noun):
    """
    Pluralizes ``noun`` depending on ``count``.  Returns only the
    noun, either pluralized or not pluralized.

    Usage:
        {{ number_of_cats|pluralize_with:"cat" }}

    Outputs:
        number_of_cats == 0: "0 cats"
        number_of_cats == 1: "1 cat"
        number_of_cats == 2: "2 cats"

    Requires the ``inflect`` module.  If it isn't available, this filter
    will not be loaded.
    """
    if not inflect:
        raise ImportError('"inflect" module is not available.  Install using `pip install inflect`.')

    return str(count) + " " + inflect.plural(noun, count)


@register.filter
def month_name(month_number):
    return calendar.month_name[month_number]
