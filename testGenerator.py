import pywikibot
from pywikibot import pagegenerators
#from pprint import pprint
site = pywikibot.Site()
cat = pywikibot.Category(site,'Category:Candidates for speedy deletion')
stuff = cat.articles()
for attr in dir(stuff):
    print("stuff.%s = %r" % (attr, getattr(stuff, attr)))
for page in stuff:
	title = stuff.title
	print(title)
